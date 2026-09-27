"""Controlled local Stage 2 replay over a frozen Stage 1 survivor pool.

Input JSON: {"jobs": [{"baseline_profile": {...}, "reviewed_profile": {...},
  "baseline_payloads": [{"candidate_id": "a", "composite_score": 0}],
  "survivor_ids": ["a"], "relevant_ids": ["a"], "split": "held_out"}],
  "candidates": [{"candidate_id": "a", "raw_cv_text": "..."}]}

The baseline uses saved campaign scores, preserving the original classifier and
query behavior. All replays use local models, with no Stage 3/provider requests.
Do not use Stage 3 model judgments as independent human relevance labels.
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.stage2_retrieval.chunker import generate_cv_chunks
from app.stage2_retrieval.embeddings import generate_embeddings, generate_single_embedding
from app.stage2_retrieval.hybrid_search import execute_category_hybrid_search
from app.stage2_retrieval.sparse import build_sparse_plan
from app.stage2_retrieval.reranker import rerank_category_chunks
from app.stage2_retrieval.evidence_extractor import (
    extract_candidate_category_evidence, resolve_retrieval_queries, DEFAULT_CATEGORY_WEIGHTS,
    rank_and_filter_candidate_batch,
)
from app.stage2_retrieval.coverage import CATEGORIES


def project_only(candidate, profile):
    chunks = generate_cv_chunks(candidate["raw_cv_text"], source_pages=candidate.get("source_pages"))
    for chunk, vector in zip(chunks, generate_embeddings([chunk["text"] for chunk in chunks])):
        chunk["embedding"] = vector
    queries = resolve_retrieval_queries(profile["jd_category_queries"], profile.get("must_have_skills", []))
    scores = {}
    for category in CATEGORIES:
        query = queries.get(category, "")
        scoped = [chunk for chunk in chunks if chunk["category"] == category]
        if not scoped and category in ("SKILLS", "PROJECTS"):
            scoped = [chunk for chunk in chunks if chunk["category"] == "EXPERIENCE"]
        if not query or not scoped:
            scores[category] = 0
            continue
        plan = build_sparse_plan(query, category, profile.get("must_have_skills"),
                                 profile.get("nice_to_have_skills"),
                                 profile.get("hard_filter_rules", {}).get("degree_requirement"))
        hits = execute_category_hybrid_search(query, generate_single_embedding(query), scoped,
                                              sparse_plan=plan)
        ranked = rerank_category_chunks(query, hits, top_n=2)
        scores[category] = max(0, sum(hit["rerank_score"] for hit in ranked) / len(ranked)) if ranked else 0
    return {"candidate_id": candidate["candidate_id"], "category_scores": scores,
            "composite_score": sum(DEFAULT_CATEGORY_WEIGHTS[cat] * scores[cat] for cat in CATEGORIES)}


def focused_legacy_score(payload):
    scores = {}
    for category in CATEGORIES:
        values = [chunk["rerank_score"] for chunk in payload["evidence_by_category"].get(category, [])
                  if "rerank_score" in chunk]
        scores[category] = max(0, sum(values) / len(values)) if values else 0
    return {"candidate_id": payload["candidate_id"], "category_scores": scores,
            "composite_score": sum(DEFAULT_CATEGORY_WEIGHTS[cat] * scores[cat] for cat in CATEGORIES)}


def metrics(payloads, selected, labels):
    ids = [payload["candidate_id"] for payload in selected]
    relevant = set(labels) if labels is not None else None
    scores = sorted((payload["composite_score"] for payload in payloads), reverse=True)
    boundary = selected[-1]["composite_score"] if selected else None
    return {"comparisons": len(payloads), "selected_ids": ids, "selected_count": len(ids),
            "precision_at_cap": len(set(ids) & relevant) / len(ids) if relevant is not None and ids else None,
            "recall_at_cap": len(set(ids) & relevant) / len(relevant) if relevant else None,
            "unrelated_selections": len(set(ids) - relevant) if relevant is not None else None,
            "zero_score_candidates": sum(score == 0 for score in scores),
            "selected_zero_score_candidates": sum(payload["composite_score"] == 0 for payload in selected),
            "cutoff_tie_size": sum(score == boundary for score in scores) if boundary is not None else 0,
            "category_zero_counts": {cat: sum(payload.get("category_scores", {}).get(cat) == 0
                                               for payload in payloads) for cat in CATEGORIES},
            "category_not_applicable_counts": {cat: sum(cat in payload.get("category_scores", {}) and payload["category_scores"][cat] is None
                                                         for payload in payloads) for cat in CATEGORIES},
            "supported_target_count": sum(payload.get("supported_target_count", 0) for payload in payloads)}


def run_replay(data):
    candidates = {candidate["candidate_id"]: candidate for candidate in data["candidates"]}
    if len(candidates) != len(data["candidates"]):
        raise ValueError("Candidate IDs must be unique")
    report = {"cap": 15, "provider_requests": 0, "calibration_status": "UNVALIDATED",
              "validation_kind": data.get("validation_kind", "campaign"), "jobs": []}
    for job in data["jobs"]:
        baseline, reviewed = job["baseline_profile"], job["reviewed_profile"]
        if baseline.get("hard_filter_rules") != reviewed.get("hard_filter_rules"):
            raise ValueError("Freeze hard filters across the controlled replay")
        if not (reviewed.get("relevance_contract") or {}).get("targets"):
            raise ValueError("Recruiter-reviewed relevance targets are required for replay")
        survivors = set(job["survivor_ids"])
        saved = job["baseline_payloads"]
        if {payload["candidate_id"] for payload in saved} != survivors or len(saved) != len(survivors):
            raise ValueError("Saved baseline must account for exactly the frozen survivor pool")
        fixed, focused, covered = [], [], []
        start = time.perf_counter()
        for identifier in sorted(survivors):
            candidate = candidates[identifier]
            fixed.append(project_only(candidate, baseline))
            payload = extract_candidate_category_evidence(identifier, candidate["raw_cv_text"],
                reviewed["jd_category_queries"], required_skills=reviewed.get("must_have_skills"),
                preferred_skills=reviewed.get("nice_to_have_skills"),
                degree_requirement=reviewed.get("hard_filter_rules", {}).get("degree_requirement"),
                relevance_contract=reviewed["relevance_contract"], source_pages=candidate.get("source_pages"))
            if payload["status"] != "SUCCESS":
                raise ValueError(f"Replay evidence failed for {identifier}; do not report it as zero relevance")
            focused.append(focused_legacy_score(payload))
            covered.append(payload)
        variants = {}
        for name, payloads in (("saved_baseline", saved), ("project_fix_only", fixed),
                               ("focused_retrieval_legacy_logits", focused), ("coverage_and_eligibility", covered)):
            selected = (rank_and_filter_candidate_batch(payloads) if name == "coverage_and_eligibility" else
                        sorted(payloads, key=lambda row: (-row["composite_score"], row["candidate_id"]))[:15])
            variants[name] = metrics(payloads, selected, job.get("relevant_ids"))
        report["jobs"].append({"job_id": reviewed["job_id"], "split": job.get("split", "unlabeled"),
                               "elapsed_seconds": round(time.perf_counter() - start, 3), "variants": variants})
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    report = run_replay(json.loads(args.input.read_text(encoding="utf-8")))
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
