import re
from typing import List, Dict, Any


def required_skill_evidence(text: str, cluster: Dict[str, Any]) -> Dict[str, Any]:
    """Term presence is evidence of a mention, never verified proficiency."""
    uncertain = None
    for terms, weight in (([cluster["canonical"], *cluster.get("aliases", [])], 1.0),
                          (cluster.get("substitutes", []), 0.75)):
        for term in terms:
            for match in re.finditer(r"(?<!\w)" + re.escape(term) + r"(?!\w)", text, re.I):
                prefix = re.split(r"[.!?\n]", text[max(0, match.start() - 80):match.start()])[-1]
                evidence = {"matched_term": term, "excerpt": text[max(0, match.start() - 80):match.end() + 80],
                            "match_weight": weight, "negated": False}
                if re.search(r"\b(no|not|without|lack|lacking|never)\b", prefix, re.I):
                    evidence.update(match_weight=0, negated=True)
                    uncertain = evidence
                else:
                    return evidence
    return uncertain or {"matched_term": None, "excerpt": None, "match_weight": 0, "negated": False}


def evaluate_skill_cluster_match(cv_text_lower: str, cluster: Dict[str, Any]) -> float:
    """
    Evaluates candidate text against a SkillCluster:
    - Exact canonical term or Alias/Synonym match = 1.0 weight
    - Acceptable Substitute match = 0.75 weight
    - No match = 0.0 weight
    """
    canonical = cluster.get("canonical", "").lower()
    aliases = [a.lower() for a in cluster.get("aliases", [])]
    substitutes = [s.lower() for s in cluster.get("substitutes", [])]

    # 1. Exact canonical or Synonym/Alias match (1.0)
    for term in [canonical] + aliases:
        if not term:
            continue
        pattern = r"(?<!\w)" + re.escape(term) + r"(?!\w)"
        if re.search(pattern, cv_text_lower):
            return 1.0

    # 2. Domain Substitute match (0.75)
    for sub in substitutes:
        if not sub:
            continue
        pattern = r"(?<!\w)" + re.escape(sub) + r"(?!\w)"
        if re.search(pattern, cv_text_lower):
            return 0.75

    return 0.0


def match_cv_against_jds(redacted_cv_text: str, active_jds: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Matches candidate CV text against active JD profiles using normalized anchor density.
    Uncapped fan-out: returns ALL JDs where match score meets or exceeds min_match_threshold.
    """
    if not redacted_cv_text or not active_jds:
        return []

    cv_text_lower = redacted_cv_text.lower()
    matched_pipelines = []

    for jd in active_jds:
        must_haves = jd.get("must_have_skills", [])
        threshold = jd.get("min_match_threshold", 0.50)

        if not must_haves:
            continue

        total_possible_weight = len(must_haves)
        earned_weight = 0.0
        matched_details = []

        for cluster in must_haves:
            score = evaluate_skill_cluster_match(cv_text_lower, cluster)
            if score > 0.0:
                earned_weight += score
                matched_details.append({
                    "canonical": cluster.get("canonical"),
                    "match_weight": score
                })

        normalized_score = earned_weight / total_possible_weight if total_possible_weight > 0 else 0.0

        if normalized_score >= threshold:
            matched_pipelines.append({
                "job_id": jd.get("job_id"),
                "job_title": jd.get("job_title"),
                "match_score": round(normalized_score, 2),
                "matched_skills": matched_details
            })

    return matched_pipelines
