import re
from typing import Any

from app.stage1_rules.contracts import (
    AttributeSource, AuthorizationStatus, CandidateFacts, DEGREE_HIERARCHY,
    HardFilterRules, resolve_hard_filters,
)
from app.stage2_retrieval.chunker import SECTION_HEADER_PATTERN

STAGE1_POLICY_VERSION = "stage1-v1.1.0"
EDUCATION_CONTEXT_ANCHORS = re.compile(
    r"(?i)\b(?:university|college|campus|institute|gpa|cgpa|graduated|degree|faculty|board|school|passed|major|specialization)\b"
)
IN_PROGRESS_PATTERNS = re.compile(
    r"(?i)\b(?:pursuing|ongoing|enrolled|expected|current|present|running|incomplete|dropped\s+out|not\s+completed)\b"
)
EDUCATION_SECTION_PATTERN = re.compile(
    r"(?im)^[ \t]*(?:education|academic\s+background|academic\s+qualifications|educational\s+background|qualifications)[ \t]*(?::[ \t]*|$)"
)
DEGREE_PATTERN = re.compile(
    r"(?i)(?<!\w)(bachelor(?:'s|s)?|master(?:'s|s)?|ph\.?d\.?|doctorate|high\s+school|"
    r"higher\s+secondary|secondary|diploma|\+2|intermediate|a-levels|12th|10th|slc|see|"
    r"b\.?tech\.?|b\.?sc\.?|b\.?e\.?|b\.?s\.?|bca|bit|bba|bim|"
    r"m\.?tech\.?|m\.?sc\.?|m\.?e\.?|m\.?s\.?|mca|mba)(?!\w)"
)


def extract_education_zone(cv_text: str) -> tuple[str, bool]:
    """Stop at the next structural heading, including headings without a colon."""
    zones = []
    for start in EDUCATION_SECTION_PATTERN.finditer(cv_text):
        next_heading = SECTION_HEADER_PATTERN.search(cv_text, start.end())
        zones.append(cv_text[start.end():next_heading.start() if next_heading else len(cv_text)])
    return ("\n\n".join(zones), True) if zones else (cv_text, False)


def _degree_matches(line):
    for match in DEGREE_PATTERN.finditer(line):
        term = match.group(1)
        cleaned = term.upper().replace(".", "").replace("'S", "")
        if cleaned in {"BACHELORS", "MASTERS"}:
            cleaned = cleaned[:-1]
        # Avoid ordinary words and job titles being interpreted as qualifications.
        if cleaned in {"BE", "ME", "SEE", "BIT"} and term.islower() and "." not in term:
            continue
        if cleaned == "MASTER" and re.search(r"(?i)\bscrum\s*$", line[:match.start()]):
            continue
        if cleaned == "DIPLOMA" and re.search(r"(?i)\bhigh\s+school\s*$", line[:match.start()]):
            continue
        key = next((key for key in DEGREE_HIERARCHY if key.replace(".", "") == cleaned), None)
        if key:
            yield key, DEGREE_HIERARCHY[key]


def parse_degree_entries(cv_text: str) -> list[dict[str, Any]]:
    zone, scoped = extract_education_zone(cv_text)
    lines = zone.splitlines()
    entries = []
    for index, line in enumerate(lines):
        matches = list(dict.fromkeys(_degree_matches(line)))
        if not matches:
            continue
        # A qualification may have its field or completion status on following lines.
        context = [line.strip()]
        if index and not list(_degree_matches(lines[index - 1])) and IN_PROGRESS_PATTERNS.search(lines[index - 1]):
            context.insert(0, lines[index - 1].strip())
        for following in lines[index + 1:index + 4]:
            if not following.strip() or list(_degree_matches(following)) or SECTION_HEADER_PATTERN.fullmatch(following):
                break
            context.append(following.strip())
        full_context = " ".join(context)
        if not scoped and not EDUCATION_CONTEXT_ANCHORS.search(full_context):
            continue
        for key, rank in matches:
            entries.append({
                "level_key": key, "level_rank": rank, "matched_text": line.strip(),
                "is_in_progress": bool(IN_PROGRESS_PATTERNS.search(full_context)),
                "is_ambiguous": len({rank for _, rank in matches}) > 1,
                "full_context": full_context,
            })
    return entries


def check_field_alignment(entry_text: str, req_fields: list[str], req_field_aliases: list[str]) -> bool:
    terms = req_fields + req_field_aliases
    return not terms or any(re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", entry_text, re.I) for term in terms)


def _has_explicit_field(entry):
    # A generic award ('Bachelor of Science') or an institution ('University of X')
    # is not a documented conflicting field. Keep ambiguous cases for review.
    line = re.split(r"(?i)\s[-|;]\s|\b(?:university|college|institute)\b", entry["matched_text"])[0]
    match = re.search(r"(?i)\b(?:of|in|major|specialization)\s+(.+)", line)
    if not match:
        return False
    field = re.sub(r"[\d\W]+", " ", match.group(1)).strip().lower()
    return field not in {"", "science", "arts", "engineering"}


def evaluate_stage1_hard_filters(
    candidate_yoe: float | None,
    candidate_cv_text: str,
    work_authorized: AuthorizationStatus | bool | None,
    jd_profile: dict[str, Any] | HardFilterRules,
    *,
    experience_source: AttributeSource = AttributeSource.UNKNOWN,
    authorization_source: AttributeSource = AttributeSource.UNKNOWN,
    required_skills: list[dict] | None = None,
) -> dict[str, Any]:
    """Invalid input raises validation errors; uncertainty is REVIEW, not a rejection."""
    rules = jd_profile if isinstance(jd_profile, HardFilterRules) else resolve_hard_filters(jd_profile)
    facts = CandidateFacts(experience_years=candidate_yoe, experience_source=experience_source,
                           work_authorized=work_authorized, authorization_source=authorization_source)
    checks = []

    def record(rule, status, code, message):
        checks.append({"rule": rule, "status": status, "code": code, "message": message})

    if not rules.require_work_authorization:
        record("authorization", "PASS", "AUTHORIZATION_NOT_REQUIRED", "JD does not require an authorization filter.")
    elif facts.work_authorized == AuthorizationStatus.UNKNOWN:
        record("authorization", "REVIEW", "AUTHORIZATION_UNKNOWN", "Work authorization is unknown.")
    elif facts.authorization_source != AttributeSource.RECRUITER_VERIFIED:
        record("authorization", "REVIEW", "AUTHORIZATION_UNVERIFIED", "Work authorization requires recruiter verification.")
    elif facts.work_authorized == AuthorizationStatus.INELIGIBLE:
        record("authorization", "FAIL", "AUTHORIZATION_INELIGIBLE", "Work authorization check failed.")
    else:
        record("authorization", "PASS", "AUTHORIZATION_ELIGIBLE", "Verified work authorization meets the requirement.")

    if rules.min_years_experience == 0:
        record("experience", "PASS", "EXPERIENCE_NOT_REQUIRED", "JD has no minimum experience requirement.")
    elif facts.experience_years is None:
        record("experience", "REVIEW", "EXPERIENCE_UNKNOWN", "Years of experience are unknown.")
    elif facts.experience_source != AttributeSource.RECRUITER_VERIFIED:
        record("experience", "REVIEW", "EXPERIENCE_UNVERIFIED", "Experience requires recruiter verification.")
    elif facts.experience_years < rules.min_years_experience:
        record("experience", "FAIL", "INSUFFICIENT_EXPERIENCE",
               f"Insufficient YoE: candidate has {facts.experience_years} years, JD requires {rules.min_years_experience} years.")
    else:
        record("experience", "PASS", "EXPERIENCE_MET", "Verified experience meets the minimum.")

    entries = parse_degree_entries(candidate_cv_text)
    requirement = rules.degree_requirement
    rank = DEGREE_HIERARCHY[requirement.level] if requirement else 0
    if not rank:
        record("education", "PASS", "EDUCATION_NOT_REQUIRED", "JD has no degree requirement.")
    elif not entries:
        record("education", "REVIEW", "EDUCATION_UNKNOWN", "No unambiguous education entry was found.")
    else:
        qualifying = [entry for entry in entries if entry["level_rank"] >= rank and not entry["is_ambiguous"]]
        matching = [entry for entry in qualifying if check_field_alignment(
            entry["full_context"], requirement.fields, requirement.field_aliases)]
        if any(not entry["is_in_progress"] for entry in matching):
            record("education", "PASS", "EDUCATION_MET", "Degree level and field meet the requirement.")
        elif matching:
            record("education", "REVIEW", "EDUCATION_IN_PROGRESS", "Required degree level and field found, but status is IN-PROGRESS or incomplete.")
        elif any(entry["is_ambiguous"] for entry in entries):
            record("education", "REVIEW", "EDUCATION_AMBIGUOUS", "Degree level and field cannot be reliably associated.")
        elif qualifying:
            # Missing field information is not evidence of a conflicting field.
            has_explicit_fields = all(_has_explicit_field(entry) for entry in qualifying)
            if has_explicit_fields:
                record("education", "FAIL", "EDUCATION_FIELD_MISMATCH", "Education field mismatch: required field not found in documented qualifications.")
            else:
                record("education", "REVIEW", "EDUCATION_FIELD_UNKNOWN", "Required degree field cannot be determined.")
        else:
            record("education", "FAIL", "EDUCATION_LEVEL_MISMATCH", "Education level mismatch: detected level is below the requirement.")

    from app.stage1_rules.jd_matcher import required_skill_evidence
    from app.stage1_rules.jd_profiler import SkillCluster
    for value in required_skills or []:
        cluster = SkillCluster.model_validate(value).model_dump()
        skill_evidence = required_skill_evidence(candidate_cv_text, cluster)
        weight = skill_evidence["match_weight"]
        record("skill", "PASS" if weight else "REVIEW", "REQUIRED_SKILL_FOUND" if weight else "REQUIRED_SKILL_UNCERTAIN",
               f"{cluster['canonical']}: " + ("approved substitute found" if weight == 0.75 else
               "term found in CV" if weight else "no term evidence; recruiter verification required"))
        checks[-1].update(canonical=cluster["canonical"], **skill_evidence, source="cv_extracted")
    failed = [check["message"] for check in checks if check["status"] == "FAIL"]
    review = [check["message"] for check in checks if check["status"] == "REVIEW"]
    return {
        "status": "FAIL" if failed else "REVIEW" if review else "PASS",
        "policy_version": STAGE1_POLICY_VERSION,
        "checks": checks, "failed_reasons": failed, "review_notes": review,
        "metrics": {"candidate_yoe": facts.experience_years, "min_required_yoe": rules.min_years_experience,
                    "work_authorized": facts.work_authorized.value,
                    "require_work_authorization": rules.require_work_authorization,
                    "experience_source": facts.experience_source.value,
                    "authorization_source": facts.authorization_source.value,
                    "parsed_degrees": entries},
    }
