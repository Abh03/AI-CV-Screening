"""Report retrieval and outcome quality from human-labeled JSON; no CV content required.

Input: {"queries": [{"relevant_ids": ["a"], "ranked_ids": ["a", "b"]}],
        "outcomes": [{"candidate_id": "a", "label": "shortlist", "decision": "shortlist"}]}
"""
import argparse
import json
from pathlib import Path


def measure(data, cutoff):
    queries = data.get("queries", [])
    outcomes = data.get("outcomes", [])
    recall = []
    for query in queries:
        relevant = set(query["relevant_ids"])
        if relevant:
            recall.append(len(relevant.intersection(query["ranked_ids"][:cutoff])) / len(relevant))
    labels = {item["candidate_id"]: item["label"] for item in outcomes}
    decisions = {item["candidate_id"]: item["decision"] for item in outcomes}
    shortlisted = {candidate for candidate, decision in decisions.items() if decision == "shortlist"}
    correct = {candidate for candidate, label in labels.items() if label == "shortlist"}
    return {
        "query_count": len(queries), "labeled_candidate_count": len(outcomes),
        "recall_at_k": sum(recall) / len(recall) if recall else None,
        "shortlist_precision": len(shortlisted & correct) / len(shortlisted) if shortlisted else None,
        "shortlist_recall": len(shortlisted & correct) / len(correct) if correct else None,
        "review_rate": sum(decision == "review" for decision in decisions.values()) / len(outcomes) if outcomes else None,
        "error_rate": sum(decision == "error" for decision in decisions.values()) / len(outcomes) if outcomes else None,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("labels", type=Path)
    parser.add_argument("--cutoff", type=int, default=30)
    args = parser.parse_args()
    print(json.dumps(measure(json.loads(args.labels.read_text(encoding="utf-8")), args.cutoff), indent=2))
