import re
from typing import Dict, Any, List, Optional, Tuple

DEGREE_HIERARCHY: Dict[str, int] = {
    "NONE": 0,
    "SEE": 1, "SLC": 1, "SECONDARY": 1, "10TH": 1,
    "+2": 2, "INTERMEDIATE": 2, "HIGHER SECONDARY": 2, "A-LEVELS": 2, "A LEVEL": 2, "12TH": 2,
    "DIPLOMA": 3,
    "BACHELOR": 4, "B.E": 4, "B.SC": 4, "BCA": 4, "BIT": 4, "BBA": 4, "BIM": 4, "B.TECH": 4, "B.S": 4, "BSC": 4, "BE": 4, "BTECH": 4,
    "MASTER": 5, "M.E": 5, "M.SC": 5, "MCA": 5, "M.TECH": 5, "MBA": 5, "M.S": 5, "MSC": 5, "ME": 5, "MTECH": 5,
    "PHD": 6, "DOCTORATE": 6
}

EDUCATION_CONTEXT_ANCHORS = re.compile(
    r"(?i)\b(?:university|college|campus|institute|gpa|cgpa|graduated|degree|faculty|board|school|passed|major|specialization)\b"
)

IN_PROGRESS_PATTERNS = re.compile(
    r"(?i)\b(?:pursuing|ongoing|enrolled|expected|current|present|running)\b"
)

EDUCATION_SECTION_PATTERN = re.compile(
    r"(?i)(?:education|academic\s+background|academic\s+qualifications|qualifications)\b(.*?)(?=\n[A-Z\s]{4,}:|\Z)",
    re.DOTALL
)


def extract_education_zone(cv_text: str) -> Tuple[str, bool]:
    """
    Extracts the dedicated EDUCATION section.
    Returns (extracted_text, is_scoped_section).
    """
    match = EDUCATION_SECTION_PATTERN.search(cv_text)
    if match and len(match.group(1).strip()) > 20:
        return match.group(1), True
    return cv_text, False


def parse_degree_entries(cv_text: str) -> List[Dict[str, Any]]:
    """
    Parses candidate qualifications into structured entries:
    [{ 'level_key': 'BACHELOR', 'level_rank': 4, 'matched_text': 'Bachelor of Science', 'is_in_progress': False, 'context_line': '...' }]
    """
    zone_text, is_scoped = extract_education_zone(cv_text)
    lines = zone_text.splitlines()
    entries = []

    degree_regex = re.compile(
        r"(?i)\b(bachelor(?:'s)?|master(?:'s)?|phd|doctorate|diploma|\+2|intermediate|slc|see|b\.?e\b|b\.?sc\b|bca|bit|bba|bim|b\.?tech\b|m\.?e\b|m\.?sc\b|mca|m\.?tech\b|mba|m\.?s\b)\b"
    )

    for i, line in enumerate(lines):
        line_clean = line.strip()
        if not line_clean:
            continue

        match = degree_regex.search(line_clean)
        if match:
            matched_term = match.group(1).upper().replace(".", "")
            
            # Map canonical degree level rank
            rank = 0
            found_key = "NONE"
            for key, level_rank in DEGREE_HIERARCHY.items():
                clean_key = key.replace(".", "")
                if clean_key == matched_term or (len(clean_key) > 2 and clean_key in matched_term):
                    if level_rank > rank:
                        rank = level_rank
                        found_key = key

            if rank == 0:
                continue

            # Context window validation if falling back to full profile
            if not is_scoped:
                context_window = " ".join(lines[max(0, i - 1): min(len(lines), i + 2)])
                if not EDUCATION_CONTEXT_ANCHORS.search(context_window):
                    continue  # Ignore unanchored terms like 'Scrum Master' outside Education zone

            is_in_progress = bool(IN_PROGRESS_PATTERNS.search(line_clean))

            entries.append({
                "level_key": found_key,
                "level_rank": rank,
                "matched_text": line_clean,
                "is_in_progress": is_in_progress,
                "full_context": line_clean
            })

    return entries


def check_field_alignment(entry_text: str, req_fields: List[str], req_field_aliases: List[str]) -> bool:
    """Checks if a degree line matches the required field or field aliases."""
    if not req_fields and not req_field_aliases:
        return True  # Any field accepted if non specified

    search_text = entry_text.lower()
    all_field_terms = [f.lower() for f in req_fields + req_field_aliases]

    for term in all_field_terms:
        if not term:
            continue
        pattern = r"\b" + re.escape(term) + r"\b"
        if re.search(pattern, search_text):
            return True

    return False


def evaluate_stage1_hard_filters(
    candidate_yoe: float,
    candidate_cv_text: str,
    work_authorized: bool,
    jd_profile: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Evaluates hard non-negotiable Stage 1 filters.
    Returns status ('PASS', 'FAIL', 'REVIEW'), reasons, and evaluation metrics.
    """
    min_yoe = jd_profile.get("min_years_experience", 0.0)
    degree_req = jd_profile.get("degree_requirement") or {}

    req_level_str = degree_req.get("level", "NONE").upper()
    req_level_rank = DEGREE_HIERARCHY.get(req_level_str, 0)
    req_fields = degree_req.get("fields", [])
    req_field_aliases = degree_req.get("field_aliases", [])

    failed_reasons = []
    review_notes = []

    # 1. Work Authorization Check
    if not work_authorized:
        failed_reasons.append("Work authorization check failed.")

    # 2. Years of Experience Check
    if candidate_yoe < min_yoe:
        failed_reasons.append(
            f"Insufficient YoE: candidate has {candidate_yoe} years, JD requires {min_yoe} years."
        )

    # 3. Multi-Degree Evaluation
    parsed_degrees = parse_degree_entries(candidate_cv_text)

    has_passing_degree = False
    has_in_progress_matching_degree = False
    has_level_match_wrong_field = False

    if req_level_rank > 0:
        if not parsed_degrees:
            failed_reasons.append(f"No education entry matching required level '{req_level_str}' found.")
        else:
            for entry in parsed_degrees:
                level_matches = entry["level_rank"] >= req_level_rank
                field_matches = check_field_alignment(entry["full_context"], req_fields, req_field_aliases)

                if level_matches and field_matches:
                    if not entry["is_in_progress"]:
                        has_passing_degree = True
                        break
                    else:
                        has_in_progress_matching_degree = True
                elif level_matches and not field_matches:
                    has_level_match_wrong_field = True

            if not has_passing_degree:
                if has_in_progress_matching_degree:
                    review_notes.append("Candidate holds required degree level and field, but status is IN-PROGRESS.")
                elif has_level_match_wrong_field:
                    failed_reasons.append(
                        f"Education field mismatch: holds required level '{req_level_str}', but field does not match required fields {req_fields}."
                    )
                else:
                    failed_reasons.append(
                        f"Education level mismatch: highest detected level is below required '{req_level_str}'."
                    )

    # Determine Decision Status
    if failed_reasons:
        status = "FAIL"
    elif review_notes:
        status = "REVIEW"
    else:
        status = "PASS"

    return {
        "status": status,
        "failed_reasons": failed_reasons,
        "review_notes": review_notes,
        "metrics": {
            "candidate_yoe": candidate_yoe,
            "min_required_yoe": min_yoe,
            "work_authorized": work_authorized,
            "parsed_degrees": parsed_degrees
        }
    }