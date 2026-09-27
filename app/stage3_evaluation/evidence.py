"""Trusted registry built from retrieved evidence, never from model output or XML text."""
import hashlib
import json
import re
from types import MappingProxyType
from typing import Mapping

from app.stage0_extraction.injection_guard import scan_for_injection_anomalies
from app.stage3_evaluation.schemas import (
    CitationCheck, EvidenceReference, EvidenceVerification, FlagType,
    LLMEvaluationOutput, SourceLocation,
)

CATEGORIES = ("SKILLS", "EXPERIENCE", "PROJECTS", "EDUCATION")
CITATION_PATTERN = re.compile(r"(?:SKILLS|EXPERIENCE|PROJECTS|EDUCATION):[1-9][0-9]*\Z")
CLAIM_SUPPORT_VERSION = "source-excerpt-v1"
_LIMITING_TERMS = re.compile(
    r"\b(?:no|not|never|without|lack|lacks|lacking|limited|basic|beginner|"
    r"introductory|familiarity|exposure|assisted|helped|supported|supervised|"
    r"supervision|academic|prototype|prototyping|course|coursework|tutorial|"
    r"learning|studying|training|aspiring|planned|planning|intended|only|"
    r"partly|partially)\b", re.IGNORECASE)


def claim_is_source_excerpt(claim: str, source: str) -> bool:
    """Accept contiguous source excerpts without dropping sentence-level limits."""
    normalize = lambda value: " ".join(value.split()).casefold()
    claim, source = normalize(claim), normalize(source)
    if not claim or not re.search(r"\w", claim):
        return False
    if claim == source:
        return True
    # Word boundaries prevent a skill such as Java matching JavaScript.
    pattern = re.compile(r"(?<!\w)" + re.escape(claim) + r"(?!\w)")
    if not pattern.search(source):
        return False
    # Normalize line wraps before locating the enclosing sentence so that a
    # prefix such as 'No\nKafka experience' cannot lose its negation.
    # Excerpts retain the limits of every sentence they touch, including
    # partially quoted boundary sentences.
    for match in pattern.finditer(source):
        supported = True
        start = 0
        boundaries = [(boundary.start(), boundary.end())
                      for boundary in re.finditer(r"(?<=[.!?])\s+", source)]
        for end, next_start in [*boundaries, (len(source), len(source))]:
            if start < match.end() and end > match.start():
                sentence = source[start:end]
                excerpt = source[max(start, match.start()):min(end, match.end())]
                if not set(_LIMITING_TERMS.findall(sentence)) <= set(_LIMITING_TERMS.findall(excerpt)):
                    supported = False
                    break
            start = next_start
        if supported:
            return True
    return False


def checked_text(value: str) -> str:
    if not isinstance(value, str) or any(
        not (c in "\t\n\r" or 0x20 <= ord(c) <= 0xD7FF
             or 0xE000 <= ord(c) <= 0xFFFD or 0x10000 <= ord(c) <= 0x10FFFF)
        for c in value
    ):
        raise ValueError("Invalid XML text")
    return value


def stable_identity(*parts) -> str:
    return hashlib.sha256(json.dumps(parts, ensure_ascii=True).encode()).hexdigest()


def build_evidence_registry(candidate_id: str, payload: dict) -> Mapping[str, EvidenceReference]:
    from app.stage3_evaluation.context import prepare_evaluation_context
    payload = prepare_evaluation_context(candidate_id, payload)
    checked_text(candidate_id)
    if payload.get("candidate_id", candidate_id) != candidate_id:
        raise ValueError("Evidence belongs to another candidate")
    evidence_map = payload.get("evidence_by_category", {})
    if not isinstance(evidence_map, dict) or set(evidence_map) - set(CATEGORIES):
        raise ValueError("Invalid evidence categories")
    registry = {}
    identities = {}
    for category in CATEGORIES:
        chunks = evidence_map.get(category, [])
        if not isinstance(chunks, list):
            raise ValueError("Evidence category must be a list")
        for index, chunk in enumerate(chunks, 1):
            if not isinstance(chunk, dict) or chunk.get("candidate_id", candidate_id) != candidate_id:
                raise ValueError("Invalid evidence ownership")
            text = checked_text(chunk.get("text", ""))
            if not text.strip():
                continue  # Empty placeholders are never citable.
            source_category = chunk.get("category", category)
            allowed_sources = ({"SKILLS", "EXPERIENCE", "PROJECTS"} if category == "SKILLS" else
                               {"EXPERIENCE", "PROJECTS"} if category in ("EXPERIENCE", "PROJECTS")
                               else {"EDUCATION"})
            allowed_sources.add("CONTEXT")  # Headerless CV text retains unclassified provenance.
            if source_category not in allowed_sources:
                raise ValueError("Invalid source category")
            location = SourceLocation.model_validate(chunk.get("source_location") or {
                "section": chunk.get("section"), "chunk_index": chunk.get("chunk_index"),
                "page_number": chunk.get("page_number"), "block_index": chunk.get("block_index"),
            })
            # Content-derived fallback is stable, but not a claim of database persistence.
            chunk_id = chunk.get("chunk_id") or "snapshot:" + stable_identity(
                candidate_id, source_category, location.model_dump(), text)
            document_id = chunk.get("document_id")
            checked_text(chunk_id)
            if not chunk_id.strip():
                raise ValueError("Empty chunk identity")
            identity = (document_id, chunk_id)
            signature = (text, source_category, location)
            if identity in identities and identities[identity] != signature:
                raise ValueError("Conflicting chunk identity")
            identities[identity] = signature
            registry[f"{category}:{index}"] = EvidenceReference(
                candidate_id=candidate_id, category=category, source_category=source_category,
                chunk_id=chunk_id, document_id=document_id, source_location=location, text=text,
                evidence_id="ev_" + stable_identity(candidate_id, category, document_id, chunk_id,
                                                     source_category, location.model_dump(), text),
            )
    return MappingProxyType(registry)


def verify_evidence(output: LLMEvaluationOutput, registry: Mapping[str, EvidenceReference],
                    candidate_id: str, injection_signals=(), *, require_claim_support=False,
                    context_complete=True) -> EvidenceVerification:
    checks = []
    reasons = set()
    verified_flags = []
    signals = set(injection_signals)
    if not context_complete:
        reasons.add("EVIDENCE_CONTEXT_TRUNCATED")
    for tag, reference in registry.items():
        if reference.candidate_id != candidate_id:
            raise ValueError("Cross-candidate registry")
        if scan_for_injection_anomalies(reference.text)["is_flagged"]:
            signals.add(f"EVIDENCE_INJECTION_SIGNAL:{tag}")

    def check_citations(field, citations, allowed_categories):
        valid = bool(citations)
        if not citations:
            reasons.add(f"MISSING_CITATIONS:{field}")
        for citation in citations:
            reference = registry.get(citation)
            reason = None
            if not CITATION_PATTERN.fullmatch(citation):
                reason = "MALFORMED_CITATION"
            elif reference is None:
                reason = "UNKNOWN_CITATION"
            elif reference.category not in allowed_categories:
                reason = "WRONG_CATEGORY"
            checks.append(CitationCheck(field=field, citation=citation, valid=reason is None, reason=reason))
            if reason:
                valid = False
                reasons.add(f"INVALID_CITATIONS:{field}")
        return valid

    def check_support(field, assessment, allowed_categories):
        normalize = lambda value: " ".join(value.split()).casefold()
        valid = True
        if require_claim_support and assessment.citations and not assessment.claims:
            reasons.add(f"MISSING_CLAIM_SUPPORT:{field}")
            valid = False
        supported = set()
        for claim in assessment.claims:
            reference = registry.get(claim.citation)
            reason = None
            if (reference is None or reference.category not in allowed_categories
                    or claim.citation not in assessment.citations):
                reason = "INVALID_CLAIM_CITATION"
            elif not claim.claim.strip() or not claim.quote.strip():
                reason = "EMPTY_CLAIM_SUPPORT"
            elif not claim.evidence_id and ("..." in claim.quote or "…" in claim.quote):
                reason = "ELLIPSIS_IN_QUOTE"
            elif normalize(claim.quote) not in normalize(reference.text):
                reason = "QUOTE_NOT_IN_SOURCE"
            elif claim.evidence_id and claim.evidence_id != reference.evidence_id:
                reason = "INVALID_EVIDENCE_ID"
            elif claim.evidence_id and not claim_is_source_excerpt(claim.claim, reference.text):
                # Overlap alone cannot establish support for paraphrased assertions.
                reason = "CLAIM_NOT_EXPLICIT_IN_SOURCE"
            elif not set(re.findall(r"\d+(?:\.\d+)?", claim.claim)).issubset(
                    set(re.findall(r"\d+(?:\.\d+)?", claim.quote))):
                reason = "NUMERIC_CLAIM_NOT_IN_QUOTE"
            checks.append(CitationCheck(field=field + ".claims", citation=claim.citation,
                                        valid=reason is None, reason=reason))
            if reason:
                reasons.add(f"UNSUPPORTED_CLAIM:{field}")
                valid = False
            else:
                supported.add(claim.citation)
        if require_claim_support and set(assessment.citations) - supported:
            reasons.add(f"MISSING_CLAIM_SUPPORT:{field}")
            valid = False
        return valid

    for category in CATEGORIES:
        if not any(ref.category == category for ref in registry.values()):
            reasons.add(f"MISSING_EVIDENCE:{category}")
        assessment = getattr(output, category.lower())
        check_citations(category.lower(), assessment.citations, {category})
        check_support(category.lower(), assessment, {category})
    for index, flag in enumerate(output.flags):
        allowed = {"EXPERIENCE"} if flag.type == FlagType.EVIDENCED_CAREER_GAP else set(CATEGORIES)
        citations_valid = check_citations(f"flags[{index}]", flag.citations, allowed)
        support_valid = check_support(f"flags[{index}]", flag, allowed)
        if citations_valid and support_valid:
            verified_flags.append(index)
    if signals:
        reasons.add("INJECTION_SIGNAL_REQUIRES_REVIEW")
    return EvidenceVerification(registry=dict(registry), checks=checks, review_reasons=sorted(reasons),
                                verified_flag_indices=verified_flags, injection_signals=sorted(signals))


def resolve_evidence_selection(selection, registry, candidate_id):
    """Attach trusted text without accepting any model-generated quotation."""
    by_id = {}
    for tag, ref in registry.items():
        if ref.candidate_id != candidate_id:
            raise ValueError("Cross-candidate registry")
        if ref.evidence_id:
            by_id[ref.evidence_id] = (tag, ref)
    data = selection.model_dump()
    for assessment in [*(data[c.lower()] for c in CATEGORIES), *data["flags"]]:
        assessment["citations"] = [by_id[c][0] if c in by_id else "UNKNOWN_ID:" + c
                                   for c in assessment["citations"]]
        for claim in assessment["claims"]:
            evidence_id = claim["citation"]
            entry = by_id.get(evidence_id)
            claim.update(citation=entry[0] if entry else "UNKNOWN_ID:" + evidence_id,
                         quote=entry[1].text if entry else "", evidence_id=evidence_id)
    return LLMEvaluationOutput.model_validate(data)


def sentence_registry(registry):
    """Split at sentence punctuation, never at PDF line wraps or decimal points.

    Keep the entire enclosing sentence (including qualifiers). Source offsets
    remain in the parent chunk's coordinates; IDs bind the exact parent and span.
    """
    result = {}
    counts = dict.fromkeys(CATEGORIES, 0)
    for ref in registry.values():
        start = 0
        for end in [m.end() for m in re.finditer(r"[!?](?=\s|$)|\.(?=\s+[A-Z]|$)", ref.text)] + [len(ref.text)]:
            text = ref.text[start:end]
            if text.strip():
                counts[ref.category] += 1
                result[f"{ref.category}:{counts[ref.category]}"] = ref.model_copy(update={
                    "text": text, "sentence_start": start, "sentence_end": end,
                    "evidence_id": "ev_" + stable_identity("sentence-v1", ref.evidence_id, start, end)[:24]})
            start = end
    return MappingProxyType(result)


def resolve_sentence_selection(selection, registry, candidate_id):
    """The provider selects evidence; only trusted Python constructs facts."""
    data = selection.model_dump()
    by_id = {ref.evidence_id: ref for ref in registry.values()}
    for assessment in [*(data[c.lower()] for c in CATEGORIES), *data["flags"]]:
        assessment["claims"] = [{"claim": by_id[c].text if c in by_id else "",
                                  "citation": c} for c in assessment["citations"]]
    from app.stage3_evaluation.schemas import EvidenceSelectionOutput
    return resolve_evidence_selection(EvidenceSelectionOutput.model_validate(data), registry, candidate_id)
