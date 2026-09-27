"""Build bounded, citable CV context independently of retrieval ranking."""
from app.stage2_retrieval.chunker import SECTION_HEADER_PATTERN, CATEGORY_MAP, normalize_header_to_canonical
from app.stage3_evaluation.evidence import stable_identity, checked_text


def unique_evidence_chars(evidence):
    """The compact prompt sends shared source chunks once across categories."""
    sources = {(chunk.get('document_id'), chunk.get('chunk_id') or stable_identity(chunk['text']),
                chunk['text']) for chunks in evidence.values() for chunk in chunks}
    return sum(len(text) for _, _, text in sources)


def prepare_evaluation_context(candidate_id, payload, max_chars=None, *, complete_units=False, mandatory_terms=()):
    from app.config import settings
    if payload.get("candidate_id", candidate_id) != candidate_id:
        raise ValueError("Evidence ownership mismatch")
    if payload.get("context_metadata") is not None:
        return payload
    text = payload.get("candidate_cv_text")
    if text is None:
        return payload  # Legacy evidence snapshots remain readable.
    checked_text(text)
    limit = settings.STAGE3_CONTEXT_MAX_CHARS if max_chars is None else max_chars
    if limit < 1:
        raise ValueError("Invalid context budget")
    evidence = {category: list(chunks) for category, chunks in
                payload.get("evidence_by_category", {}).items()}
    used = unique_evidence_chars(evidence)
    omitted_retrieval = []
    # Keep complete source chunks; never turn truncation into a fabricated quote.
    while used > limit:
        category = max(evidence, key=lambda cat: sum(len(c["text"]) for c in evidence[cat]))
        removed = evidence[category].pop()
        omitted_retrieval.append(removed.get("chunk_id", "retrieved:" + stable_identity(removed["text"])))
        used = unique_evidence_chars(evidence)
    document_id = "context:" + stable_identity(candidate_id, text)
    headers = list(SECTION_HEADER_PATTERN.finditer(text))
    boundaries = [(0, "UNCLASSIFIED")]
    boundaries.extend((match.start(), normalize_header_to_canonical(match.group(1))) for match in headers)
    passages = []
    for index, (start, section) in enumerate(boundaries):
        end = boundaries[index + 1][0] if index + 1 < len(boundaries) else len(text)
        while start < end:
            stop = end if complete_units else min(start + 3000, end)
            if stop < end:
                boundary = text.rfind("\n", start + 1500, stop)
                if boundary >= 0:
                    stop = boundary + 1
            body = text[start:stop]
            if body.strip():
                category = CATEGORY_MAP.get(section, "CONTEXT")
                passages.append({"candidate_id": candidate_id, "document_id": document_id,
                    "chunk_id": "context-chunk:" + stable_identity(document_id, start, stop),
                    "section": section, "category": category, "text": body,
                    "source_location": {"section": section, "char_start": start, "char_end": stop}})
            start = stop
    # Source order preserves chronology; round-robin sections prevents a long
    # experience section from exhausting the budget before projects/education.
    queues = {category: [] for category in ("CONTEXT", "EXPERIENCE", "PROJECTS", "SKILLS", "EDUCATION")}
    for passage in passages:
        queues[passage["category"]].append(passage)
    ordered = []
    while any(queues.values()):
        for queue in queues.values():
            if queue:
                ordered.append(queue.pop(0))
    # For oversized CVs, put the full source passage around retrieval hits and
    # its neighbours ahead of the remaining section-balanced context.
    import re
    focused_texts = [" ".join(re.sub(r"^\[Section: [^]]+\]\s*", "", chunk["text"]).split())
                     for chunks in evidence.values() for chunk in chunks]
    anchors = {index for index, passage in enumerate(passages)
               if any(body and body in " ".join(passage["text"].split()) for body in focused_texts)}
    nearby = {index + delta for index in anchors for delta in (-1, 0, 1)
              if 0 <= index + delta < len(passages)
              and passages[index + delta]["section"] == passages[index]["section"]}
    priority = [passages[index] for index in sorted(nearby)]
    priority_ids = {passage["chunk_id"] for passage in priority}
    ordered = priority + [passage for passage in ordered if passage["chunk_id"] not in priority_ids]
    if mandatory_terms:
        ordered.sort(key=lambda passage: not any(term.casefold() in passage["text"].casefold()
                                                  for term in mandatory_terms))
    included = 0
    omitted = omitted_retrieval
    for passage in ordered:
        source = passage["category"]
        categories = (["EXPERIENCE", "PROJECTS", "SKILLS", "EDUCATION"] if source == "CONTEXT"
                      else ["EXPERIENCE", "PROJECTS", "SKILLS"] if source == "EXPERIENCE"
                      else ["PROJECTS", "EXPERIENCE", "SKILLS"] if source == "PROJECTS" else [source])
        # The provider prompt shares source text across category citation tags.
        cost = len(passage["text"])
        if used + cost > limit:
            omitted.append(passage["chunk_id"])
            continue
        for category in categories:
            evidence.setdefault(category, []).append(dict(passage))
        used += cost
        included += len(passage["text"])
    return dict(payload, evidence_by_category=evidence, context_metadata={
        "version": "citable-cv-v3", "source_chars": len(text),
        "budget_basis": "unique_context_and_retrieved_chars",
        "included_context_chars": included, "evidence_chars": used,
        "omitted_passage_ids": omitted, "complete": not omitted,
        "require_claim_support": True})


def bounded_evaluation_prompt(candidate_id, jd_profile, payload, *, max_bytes=None, sentence_selections=False):
    """Bound escaped XML too, with the registry built from exactly what is sent."""
    from app.config import settings
    from app.stage3_evaluation.evidence import build_evidence_registry
    from app.stage3_evaluation.prompts import build_stage3_user_prompt
    limit = settings.STAGE3_PROMPT_MAX_BYTES if max_bytes is None else max_bytes
    prepared = prepare_evaluation_context(candidate_id, payload, complete_units=sentence_selections,
        mandatory_terms=[skill["canonical"] for skill in jd_profile.get("must_have_skills", [])])
    evidence = {cat: list(chunks) for cat, chunks in prepared.get("evidence_by_category", {}).items()}
    metadata = dict(prepared.get("context_metadata") or {})
    metadata["omitted_passage_ids"] = list(metadata.get("omitted_passage_ids", []))
    while True:
        prepared = dict(prepared, evidence_by_category=evidence, context_metadata=metadata)
        registry = build_evidence_registry(candidate_id, prepared)
        if sentence_selections:
            from app.stage3_evaluation.evidence import sentence_registry
            registry = sentence_registry(registry)
        prompt = build_stage3_user_prompt(candidate_id, jd_profile, prepared, registry=registry,
                                         compact=True, neutral_sources=True)
        from app.stage3_evaluation.budget import request_budget
        budget = request_budget(prompt, settings.LLM_PROVIDER.lower()) if sentence_selections else None
        if len(prompt.encode("utf-8")) <= limit and (budget is None or budget["total"] <= budget["limit"]):
            if budget is not None:
                metadata["token_budget"] = budget
            return prepared, registry, prompt
        choices = [(cat, index, chunk) for cat, chunks in evidence.items()
                   for index, chunk in enumerate(chunks)]
        if not choices:
            raise ValueError("JD and prompt metadata exceed the provider prompt budget")
        # Remove an entire source group together, preserving complete context.
        # Mandatory terms and role/project context outrank optional retrieval.
        mandatory = [skill['canonical'].casefold() for skill in jd_profile.get('must_have_skills', [])]
        groups = {}
        for cat, index, chunk in choices:
            key = (chunk.get('document_id'), chunk.get('chunk_id'), chunk['text'])
            groups.setdefault(key, []).append((cat, index, chunk))
        def priority(group):
            body = group[0][2]['text'].casefold()
            return (any(term in body for term in mandatory),
                    str(group[0][2].get('document_id', '')).startswith('context:'),
                    any(cat in ('EXPERIENCE', 'PROJECTS') for cat, _, _ in group),
                    -len(body))
        removed = min(groups.values(), key=priority)
        for cat, index, chunk in sorted(removed, key=lambda item: item[1], reverse=True):
            evidence[cat].pop(index)
            metadata['omitted_passage_ids'].append(chunk.get('chunk_id') or stable_identity(chunk['text']))
        metadata["complete"] = False
        metadata["require_claim_support"] = True
        metadata["prompt_omitted_chunk_count"] = metadata.get("prompt_omitted_chunk_count", 0) + len(removed)
        metadata["evidence_chars"] = unique_evidence_chars(evidence)
