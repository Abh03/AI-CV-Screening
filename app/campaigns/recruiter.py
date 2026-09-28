"""Recruiter projections. Screening snapshots stay immutable.

Pool projections deliberately omit PDF bytes, full CV text and full assessments.
The bounded campaign pool is filtered before pagination, using one portable
implementation for SQLite tests and PostgreSQL deployments.
"""
from collections import Counter
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.api.campaigns import _pair_view


class PoolFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")
    search: str = Field(default="", max_length=255)
    status: str = Field(default="", max_length=32)
    decision: Literal["", "UNREVIEWED", "SHORTLIST", "HOLD", "NOT_PROCEEDING"] = ""
    min_score: float | None = Field(default=None, ge=0, le=100, allow_inf_nan=False)
    max_score: float | None = Field(default=None, ge=0, le=100, allow_inf_nan=False)
    category: Literal["", "skills", "experience", "projects", "education"] = ""
    min_category_score: float | None = Field(default=None, ge=0, le=100, allow_inf_nan=False)
    min_years: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    max_years: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    experience_unknown: bool = False
    eligibility_rule: Literal["", "authorization", "experience", "education", "skill"] = ""
    eligibility_status: Literal["", "PASS", "REVIEW", "FAIL"] = ""
    requirement_ids: list[str] = Field(default_factory=list, max_length=100)
    requirement_status: Literal["DIRECT", "PARTIAL", "MISSING", "UNASSESSED"] = "DIRECT"
    requirement_mode: Literal["all", "any"] = "all"
    review_only: bool = False
    reason: str = Field(default="", max_length=255)
    tag: str = Field(default="", max_length=50)
    assignee_id: str = Field(default="", max_length=64)
    reviewer_id: str = Field(default="", max_length=64)
    reviewed_after: str = Field(default="", max_length=10, pattern=r"^(\d{4}-\d{2}-\d{2})?$")
    sort: Literal["score", "name", "experience", "skills", "projects", "education", "updated"] = "score"
    direction: Literal["asc", "desc"] = "desc"

    @model_validator(mode="after")
    def ranges(self):
        for low, high in ((self.min_score, self.max_score), (self.min_years, self.max_years)):
            if low is not None and high is not None and low > high:
                raise ValueError("Minimum must not exceed maximum")
        if self.min_category_score is not None and not self.category:
            raise ValueError("Choose a category for the category score filter")
        return self


def review_view(review):
    if review is None:
        return {"decision": "UNREVIEWED", "notes": "", "reason": "", "tags": [],
                "verified_facts": {}, "assignee_id": None, "version": 0, "reviewer_id": None, "updated_at": None}
    return {key: getattr(review, key) for key in
            ("decision", "notes", "reason", "tags", "verified_facts", "assignee_id", "version", "reviewer_id", "updated_at")}


def stage_history(pair, cv):
    """Show observed stage outcomes, never invented stage timestamps."""
    extracted = cv.stage0_status == "SUCCEEDED"
    stage1 = pair.stage1_decision
    stage2 = (pair.result_snapshot or {}).get("stage2_evidence")
    stage3 = (pair.result_snapshot or {}).get("stage3_evaluation")
    return [
        {"stage": "CV extraction", "state": "IN_PROGRESS" if cv.stage0_status == "RUNNING" else cv.stage0_status, "reason": cv.extraction_error_code},
        {"stage": "Eligibility checks", "state": stage1 or ("IN_PROGRESS" if extracted and pair.status == "RUNNING" else "NOT_REACHED"),
         "reason": "; ".join(check.get("message", "") for check in (pair.stage1_details or {}).get("checks", []) if check.get("status") != "PASS")},
        {"stage": "Job requirement matching", "state": "COMPLETED" if stage2 else ("IN_PROGRESS" if stage1 in {"PASS", "REVIEW"} and pair.status == "RUNNING" else "NOT_REACHED"),
         "reason": pair.failure_code if pair.status == "PROCESSING_FAILED" else None},
        {"stage": "Detailed assessment", "state": (stage3 or {}).get("evaluation_status") or pair.stage3_status or
            ("NOT_SELECTED" if pair.status == "CUTOFF_EXCLUDED" else "IN_PROGRESS" if pair.status == "STAGE3_RUNNING" else
             "QUEUED" if pair.status == "SHORTLISTED" else "NOT_REACHED"),
         "reason": "Outside this role's detailed assessment limit; not a recruiter rejection." if pair.status == "CUTOFF_EXCLUDED" else
             pair.failure_code if pair.status in {"EVALUATION_FAILED", "REVIEW_REQUIRED"} else None},
    ]


def candidate_view(pair, cv, jd, review=None, *, detail=False):
    result = _pair_view(pair, cv.candidate_id, cv.source_filename)
    result["stage2_target_assessments"] = [{**target, "status": "MISSING" if target.get("status") == "MISSING_INFORMATION" else target.get("status")}
                                            for target in result["stage2_target_assessments"]]
    evaluation = (pair.result_snapshot or {}).get("stage3_evaluation") or {}
    raw = evaluation.get("llm_raw_output") or {}
    metrics = (pair.stage1_details or {}).get("metrics") or {}
    verified = review.verified_facts if review else {}
    effective_checks = [dict(check) for check in result["stage1_checks"]]
    rules = jd.job_snapshot.get("hard_filter_rules", {})
    for check in effective_checks:
        status = None
        if check.get("rule") == "authorization" and rules.get("require_work_authorization", True) and verified.get("work_authorized") in {"eligible", "ineligible"}:
            status = "PASS" if verified["work_authorized"] == "eligible" else "FAIL"
        if check.get("rule") == "experience" and verified.get("experience_years") is not None:
            status = "PASS" if verified["experience_years"] >= rules.get("min_years_experience", 0) else "FAIL"
        if check.get("rule") == "education" and verified.get("education_meets_requirement") is not None:
            status = "PASS" if verified["education_meets_requirement"] else "FAIL"
        if status:
            check.update(status=status, source="recruiter_verified", message="Recruiter-verified fact; the original screening assessment is retained.")
    result.update(pair_id=pair.id, campaign_id=pair.campaign_id, jd_key=jd.jd_key,
                  role_title=jd.job_snapshot.get("title", jd.jd_key),
                  experience_years=metrics.get("candidate_yoe"), experience_source=metrics.get("experience_source", "unknown"),
                  cv_available=cv.document_available,
                  stage0_status=cv.stage0_status, updated_at=pair.updated_at,
                  review=review_view(review), summary=raw.get("executive_summary", ""),
                  flags=raw.get("flags", []), scoring_policy_version=evaluation.get("scoring_policy_version"),
                  stage_history=stage_history(pair, cv))
    result["effective_eligibility_checks"] = effective_checks
    result["effective_experience_years"] = verified.get("experience_years") if verified.get("experience_years") is not None else metrics.get("candidate_yoe")
    confirmed = {check.get("rule") for check in effective_checks if check.get("source") == "recruiter_verified" and check.get("status") == "PASS"}
    result["original_verification_reasons"] = list(result["verification_reasons"])
    result["verification_reasons"] = [reason for reason in result["verification_reasons"] if not isinstance(reason, dict) or reason.get("rule") not in confirmed]
    if confirmed:
        result["verification_required"] = bool(result["verification_reasons"])
        result["provisional"] = pair.status == "SUCCESS" and result["verification_required"]
    if detail:
        result["assessments"] = {key: raw[key] for key in ("skills", "experience", "projects", "education") if key in raw}
        registry = (evaluation.get("evidence_verification") or {}).get("registry") or {}
        result["evidence"] = [{**ref, "text": registry.get(ref["citation"], {}).get("text", "")} for ref in result["evidence"]]
        result["experience_evidence"] = metrics.get("experience_evidence")
    else:
        result.pop("evidence", None)
    return result


def requirement_state(target):
    return "UNASSESSED" if target is None else target.get("status", "MISSING")


def matches(row, filters: PoolFilters, searchable_text=""):
    f = filters
    if f.search.strip().casefold() not in searchable_text.casefold():
        return False
    if f.status and row["status"] != f.status or f.decision and row["review"]["decision"] != f.decision:
        return False
    score, years = row["score"], row["effective_experience_years"]
    for value, low, high in ((score, f.min_score, f.max_score), (years, f.min_years, f.max_years)):
        if (low is not None or high is not None) and (value is None or low is not None and value < low or high is not None and value > high):
            return False
    if f.experience_unknown and years is not None:
        return False
    if f.min_category_score is not None:
        value = row["category_scores"].get(f.category)
        if value is None or value < f.min_category_score:
            return False
    if f.eligibility_rule or f.eligibility_status:
        if not any((not f.eligibility_rule or c.get("rule") == f.eligibility_rule) and
                   (not f.eligibility_status or c.get("status") == f.eligibility_status) for c in row["effective_eligibility_checks"]):
            return False
    targets = {t["target_id"]: t for t in row.get("stage2_target_assessments", [])}
    if f.requirement_ids:
        selected = [requirement_state(targets.get(key)) == f.requirement_status for key in f.requirement_ids]
        if not (all(selected) if f.requirement_mode == "all" else any(selected)):
            return False
    if f.review_only and not (row["verification_required"] or row["status"] == "REVIEW_REQUIRED"):
        return False
    if f.reason:
        reasons = " ".join(str(r) for r in row["review_reasons"] + row["verification_reasons"] + row["flags"] + row["stage1_checks"])
        reasons += " " + str(row["failure_code"] or "")
        if f.reason.casefold() not in reasons.casefold():
            return False
    review = row["review"]
    if f.tag and f.tag.casefold() not in [tag.casefold() for tag in review["tags"]]:
        return False
    if f.assignee_id and review["assignee_id"] != f.assignee_id or f.reviewer_id and review["reviewer_id"] != f.reviewer_id:
        return False
    if f.reviewed_after and (review["updated_at"] is None or str(review["updated_at"])[:10] < f.reviewed_after):
        return False
    return True


def sorted_pool(rows, filters):
    key = filters.sort
    def value(row):
        if key == "name":
            return row["source_filename"].casefold()
        if key == "updated":
            return str(row["review"]["updated_at"] or row["updated_at"])
        if key == "experience":
            return row["effective_experience_years"]
        return row["score"] if key == "score" else row["category_scores"].get(key)
    known = [row for row in rows if value(row) is not None]
    unknown = [row for row in rows if value(row) is None]
    known.sort(key=lambda row: row["candidate_id"])
    known.sort(key=value, reverse=filters.direction == "desc")
    return known + sorted(unknown, key=lambda row: row["candidate_id"])


def analytics(rows, jd):
    targets = jd.job_snapshot.get("relevance_contract", {}).get("targets", [])
    counts = Counter(row["status"] for row in rows)
    assessed = sum(row["score"] is not None for row in rows)
    distribution = [sum(row["score"] is not None and low <= row["score"] < high for row in rows)
                    for low, high in ((0, 55), (55, 75), (75, 101))]
    requirements = []
    for target in targets:
        states = Counter(requirement_state(next((t for t in row.get("stage2_target_assessments", []) if t["target_id"] == target["target_id"]), None)) for row in rows)
        requirements.append({"target_id": target["target_id"], "text": target["text"],
                             "treatment": target["treatment"], "counts": dict(states)})
    return {"total": len(rows), "assessed": assessed, "status_counts": dict(counts),
            "review_needed": sum(row["verification_required"] or row["status"] == "REVIEW_REQUIRED" for row in rows),
            "shortlisted": sum(row["review"]["decision"] == "SHORTLIST" for row in rows),
            "decision_counts": dict(Counter(row["review"]["decision"] for row in rows)),
            "score_distribution": distribution, "requirements": requirements}
