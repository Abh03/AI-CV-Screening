"""Offline repair verification using persisted source/evidence, without truth labels or APIs."""
import argparse
from collections import Counter
from datetime import date
import json
from pathlib import Path

from app.stage1_rules.experience import extract_experience
from app.stage2_retrieval.coverage import resolve_targets, score_coverage
from app.stage2_retrieval.evidence_extractor import DEFAULT_CATEGORY_WEIGHTS
from app.stage3_evaluation.context import bounded_evaluation_prompt


def replay(source):
    data = json.loads(source.read_text(encoding="utf-8-sig"))
    totals = {}
    experiences = {}
    prompts = []
    for row in data["comparisons"]:
        jd = row["stage2"]["input"]["job_profile"]
        text = row["stage0"]["output"]
        if row["cv"] not in experiences:
            fact = extract_experience(text, as_of=date(2026, 9, 27))
            experiences[row["cv"]] = ({key: fact.get(key) for key in ("years", "months", "method")}
                                        if fact else None)
        evidence = row["stage2"]["output"]
        if not evidence:
            continue
        totals.setdefault(row["jd"], Counter())
        counts = totals[row["jd"]]
        counts["stage2_survivors"] += 1
        targets, minimum = resolve_targets(jd["jd_category_queries"], jd.get("must_have_skills"),
            jd.get("nice_to_have_skills"), relevance_contract=jd.get("relevance_contract"))
        result = score_coverage(targets, evidence["evidence_by_category"], DEFAULT_CATEGORY_WEIGHTS, minimum)
        counts["previous_eligible"] += bool(evidence.get("shortlist_eligible"))
        counts["repaired_eligible_retained_evidence"] += result["shortlist_eligible"]
        fact = experiences[row["cv"]]
        rules = jd.get("hard_filter_rules") or {}
        minimum_years = rules.get("min_years_experience", 0)
        if fact and fact["years"] < minimum_years:
            counts["dated_experience_below_minimum"] += 1
        if row["stage3"]["attempts"]:
            prepared, registry, prompt = bounded_evaluation_prompt(evidence["candidate_id"], jd,
                dict(evidence, candidate_cv_text=text))
            prompts.append({"cv": row["cv"], "jd": row["jd"],
                "previous_prompt_bytes": len(row["stage3"]["input_reconstructed"].encode("utf-8")),
                "repaired_prompt_bytes": len(prompt.encode("utf-8")),
                "complete": prepared["context_metadata"]["complete"]})
    return {"mode": "offline_retained_evidence_replay", "jds": totals,
            "experience": experiences, "prompts": prompts,
            "limits": ["No new extraction, retrieval, provider calls, or hidden labels used.",
                       "New delivery queries may retrieve additional evidence during a fresh run.",
                       "Eligibility counts precede applying the new Stage 1 experience filter.",
                       "Prompt budgets reduce payload size; provider acceptance needs a live pilot."]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    result = replay(args.source)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({"jds": result["jds"], "prompts": result["prompts"]}, indent=2))
