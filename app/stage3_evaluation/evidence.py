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
            elif normalize(claim.quote) not in normalize(reference.text):
                reason = "QUOTE_NOT_IN_SOURCE"
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
