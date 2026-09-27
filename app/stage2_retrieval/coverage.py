"""Conservative evidence coverage; reranker logits never enter candidate scores.

This assessor recognizes reviewed lexical equivalents, not inferred semantic
similarity. Unstated evidence stays uncertain and can be reviewed by a recruiter.
"""
import math
import re
from app.stage1_rules.relevance import RelevanceContract
from app.stage1_rules.jd_matcher import identity_terms, term_pattern
from app.stage2_retrieval.sparse import build_sparse_plan, STOP_WORDS, normalize_lexical_v1
from app.config import settings

SCORING_VERSION = "stage2-coverage-v2"
MAX_CANDIDATES_PER_JD = 15
CATEGORIES = ("SKILLS", "EXPERIENCE", "PROJECTS", "EDUCATION")
ACTION = re.compile(r"\b(built|build(?:ing|s)?|develop(?:ed|ing|s)?|implement(?:ed|ing|s)?|"
                    r"design(?:ed|ing|s)?|deploy(?:ed|ing|s)?|operat(?:e|ed|ing|es)|"
                    r"maintain(?:ed|ing|s)?|test(?:ed|ing|s)?|automat(?:e|ed|ing|es)|"
                    r"deliver(?:ed|ing|s)?|integrat(?:e|ed|ing|es)|led|lead(?:ing|s)?|"
                    r"manag(?:e|ed|ing|es)|architect(?:ed|ing|s)?|optimiz(?:e|ed|ing|es)|"
                    r"support(?:ed|ing|s)?|resolv(?:e|ed|ing|es)|creat(?:e|ed|ing|es)|"
                    r"migrat(?:e|ed|ing|es)|used?|using|review(?:ed|ing|s)?|"
                    r"document(?:ed|ing|s)?|introduc(?:e|ed|ing|es)|add(?:ed|ing|s)?|"
                    r"investigat(?:e|ed|ing|es)|refactor(?:ed|ing|s)?|"
                    r"reduc(?:e|ed|ing|es)|troubleshoot(?:ing|s)?|configure(?:d|s)?)\b", re.I)


def data(value):
    return value if isinstance(value, dict) else value.model_dump(mode="json")


def matching(term, text):
    normalized = " " + normalize_lexical_v1(text) + " "
    return any(re.search(term_pattern(alias), text, re.I) or
               (" " + normalize_lexical_v1(alias) + " ") in normalized
               for alias in identity_terms([term]) if normalize_lexical_v1(alias))


def resolve_targets(queries, required_skills=None, preferred_skills=None,
                    degree_requirement=None, relevance_contract=None):
    """Legacy targets are marked derived; they cannot create a hard requirement."""
    contract = RelevanceContract.model_validate(relevance_contract or {})
    targets = [target.model_dump(mode="json") | {"origin": "approved"}
               for target in contract.targets]
    explicit_categories = {target["category"] for target in targets if target["treatment"] == "requirement"}
    # Applied use of mandatory technologies is useful retrieval guidance even
    # when approved responsibilities bundle several domain-specific concepts.
    if "EXPERIENCE" in explicit_categories:
        for index, cluster in enumerate(required_skills or []):
            cluster = data(cluster)
            targets.append(dict(target_id=f"derived_delivery_{index}", category="EXPERIENCE",
                kind="experience", text="Hands-on delivery using " + cluster["canonical"],
                evidence_terms=[[cluster["canonical"], *cluster.get("aliases", [])]],
                substitutes=cluster.get("substitutes", []), importance=1,
                treatment="requirement", origin="derived"))
    for category in CATEGORIES:
        if category in explicit_categories:
            continue
        if category in ("SKILLS", "EXPERIENCE") and required_skills:
            for index, cluster in enumerate(required_skills):
                cluster = data(cluster)
                targets.append(dict(target_id=f"derived_{category.lower()}_{index}", category=category,
                    kind="skill" if category == "SKILLS" else "experience",
                    text=(cluster["canonical"] if category == "SKILLS" else
                          "Hands-on delivery using " + cluster["canonical"]),
                    evidence_terms=[[cluster["canonical"], *cluster.get("aliases", [])]],
                    substitutes=cluster.get("substitutes", []), importance=1,
                    treatment="requirement", origin="derived"))
            if category == "SKILLS":
                for index, cluster in enumerate(preferred_skills or []):
                    cluster = data(cluster)
                    targets.append(dict(target_id=f"derived_preferred_{index}", category=category,
                        kind="skill", text=cluster["canonical"],
                        evidence_terms=[[cluster["canonical"], *cluster.get("aliases", [])]],
                        importance=1, treatment="preference", origin="derived"))
            continue
        query = queries.get(category, "")
        if not query.strip() or "no explicit requirement" in query.casefold():
            continue
        # Minimum years are handled in Stage 1; they are not passage relevance.
        if category == "EXPERIENCE" and re.search(r"\byears?\b", query, re.I):
            continue
        plan = build_sparse_plan(query, category, degree_requirement=degree_requirement)
        groups = [list(concept.alternatives) for concept in plan.concepts
                  if concept.name.casefold() not in STOP_WORDS]
        if groups:
            targets.append(dict(target_id=f"legacy_{category.lower()}", category=category,
                kind={"SKILLS": "skill", "PROJECTS": "project"}.get(category, category.lower()), text=query,
                evidence_terms=groups[:8], importance=1, treatment="requirement", origin="derived"))
    return targets, contract.minimum_coverage


def assess_target(target, chunks):
    best = {"target_id": target["target_id"], "category": target["category"],
            "target_text": target["text"],
            "origin": target["origin"], "status": "MISSING_INFORMATION", "coverage": 0.0,
            "citations": [], "matched_groups": [], "supporting_text": "", "accepted_substitute": False}
    for chunk in chunks:
        text = chunk.get("text", "")
        # Assess one sentence/line at a time: do not join unrelated claims.
        for passage in re.split(r"[\n;]|(?<=[.!?])\s+(?=[A-Z])", text):
            groups = target["evidence_terms"]
            matches = [index for index, terms in enumerate(groups)
                       if any(matching(term, passage) for term in terms)]
            coverage = len(matches) / len(groups)
            substitute = False
            if not matches and any(matching(term, passage) for term in target.get("substitutes", [])):
                coverage, substitute = 0.5, True
            if target["kind"] in ("experience", "responsibility", "project"):
                if chunk.get("category") not in ("EXPERIENCE", "PROJECTS") or not ACTION.search(passage):
                    continue
            if re.search(r"\b(no|not|without|lack|lacking|never)\b", passage, re.I):
                # Conservative: negated statements cannot establish positive coverage.
                continue
            if coverage > best["coverage"]:
                best.update(status="DIRECT" if coverage == 1 and not substitute else "PARTIAL",
                            coverage=coverage, citations=[chunk["chunk_id"]],
                            matched_groups=matches, supporting_text=passage, accepted_substitute=substitute)
    return best


def score_coverage(targets, evidence, weights, minimum_coverage=0):
    assessments = [assess_target(target, evidence.get(target["category"], [])) for target in targets]
    category_scores, active_weights = {}, {}
    for category in CATEGORIES:
        rows = [(target, result) for target, result in zip(targets, assessments)
                if target["category"] == category]
        if not rows:
            category_scores[category] = None
            continue
        main = [(target, result) for target, result in rows if target["treatment"] != "preference"]
        prefs = [(target, result) for target, result in rows if target["treatment"] == "preference"]
        if not main:
            category_scores[category] = None
            continue  # Preferences cannot activate an otherwise inapplicable category.
        def average(items):
            return (sum(target["importance"] * result["coverage"] for target, result in items)
                    / sum(target["importance"] for target, _ in items)) if items else 0
        # Preferences (including domains) contribute at most 10% of a category.
        category_scores[category] = (min(1.0, average(main) + 0.1 * average(prefs))
                                     if prefs else average(main))
        active_weights[category] = weights.get(category, 0)
    total = sum(active_weights.values())
    score = (sum(active_weights[cat] * category_scores[cat] for cat in active_weights) / total
             if total else 0)
    supported = sum(result["coverage"] > 0 for target, result in zip(targets, assessments)
                    if target["treatment"] != "preference")
    applied_targets = [(target, result) for target, result in zip(targets, assessments)
                       if target["kind"] in ("experience", "responsibility", "project")
                       and target["treatment"] != "preference"]
    # Shared generic anchors alone must not establish delivery relevance.
    # Require direct applied evidence, an accepted substitute, or at least two
    # concepts covering half a bundled target in the same applied passage.
    applied_supported = not applied_targets or any(result["status"] == "DIRECT" or result["accepted_substitute"]
                                                   or (result["coverage"] >= 0.5 and len(result["matched_groups"]) >= 2)
                                                   for _, result in applied_targets)
    eligible = supported > 0 and applied_supported and score > 0 and score >= minimum_coverage
    source_context_review = not any(target["origin"] == "approved" for target in targets)
    if settings.ENVIRONMENT.lower() == "production" and source_context_review:
        eligible = False
    return {"scoring_version": SCORING_VERSION, "composite_score": round(score, 6),
            "category_scores": category_scores, "target_assessments": assessments,
            "active_category_weights": {cat: value / total for cat, value in active_weights.items()} if total else {},
            "shortlist_eligible": eligible,
            "relevance_review_required": not targets or not eligible,
            "source_context_review_required": source_context_review,
            "relevance_reason": ("SOURCE_CONTEXT_REVIEW_REQUIRED" if source_context_review and settings.ENVIRONMENT.lower() == "production" else
                                  "NO_RELEVANCE_TARGETS" if not targets else
                                  "INSUFFICIENT_SUPPORTED_RELEVANCE" if not eligible else None),
            "supported_target_count": supported,
            "responsibility_coverage": sum(result["coverage"] for target, result in zip(targets, assessments)
                                           if target["kind"] == "responsibility"),
            "applied_coverage": sum(result["coverage"] for target, result in zip(targets, assessments)
                                    if target["kind"] in ("experience", "project")),
            "calibration_status": "UNVALIDATED"}


def candidate_sort_key(payload):
    score = payload.get("composite_score", 0)
    if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score):
        raise ValueError("Invalid candidate coverage score")
    return (-score, -payload.get("responsibility_coverage", 0),
            -payload.get("applied_coverage", 0), str(payload["candidate_id"]))


def eligible(payload):
    # Legacy payloads may be replayed but cannot auto-select on obsolete logits.
    score = payload.get("composite_score", 0)
    return (payload.get("scoring_version") == SCORING_VERSION and payload.get("shortlist_eligible") is True
            and isinstance(score, (int, float)) and not isinstance(score, bool) and 0 < score <= 1)
