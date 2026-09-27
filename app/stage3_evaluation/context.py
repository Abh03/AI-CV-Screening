"""Build bounded, citable CV context independently of retrieval ranking."""
from app.stage2_retrieval.chunker import SECTION_HEADER_PATTERN, CATEGORY_MAP, normalize_header_to_canonical
from app.stage3_evaluation.evidence import stable_identity, checked_text


def prepare_evaluation_context(candidate_id, payload, max_chars=None):
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
    used = sum(len(chunk["text"]) for chunks in evidence.values() for chunk in chunks)
    if used > limit:
        raise ValueError("Retrieved evidence exceeds context budget")
    document_id = "context:" + stable_identity(candidate_id, text)
    headers = list(SECTION_HEADER_PATTERN.finditer(text))
    boundaries = [(0, "UNCLASSIFIED")]
    boundaries.extend((match.start(), normalize_header_to_canonical(match.group(1))) for match in headers)
    passages = []
    for index, (start, section) in enumerate(boundaries):
        end = boundaries[index + 1][0] if index + 1 < len(boundaries) else len(text)
        while start < end:
            stop = min(start + 3000, end)
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
    included = 0
    omitted = []
    for passage in ordered:
        source = passage["category"]
        categories = (["EXPERIENCE", "PROJECTS", "SKILLS", "EDUCATION"] if source == "CONTEXT"
                      else ["EXPERIENCE", "PROJECTS", "SKILLS"] if source == "EXPERIENCE"
                      else ["PROJECTS", "EXPERIENCE", "SKILLS"] if source == "PROJECTS" else [source])
        cost = len(passage["text"]) * len(categories)
        if used + cost > limit:
            omitted.append(passage["chunk_id"])
            continue
        for category in categories:
            evidence.setdefault(category, []).append(dict(passage))
        used += cost
        included += len(passage["text"])
    return dict(payload, evidence_by_category=evidence, context_metadata={
        "version": "citable-cv-v1", "source_chars": len(text),
        "included_context_chars": included, "evidence_chars": used,
        "omitted_passage_ids": omitted, "complete": not omitted,
        "require_claim_support": True})
