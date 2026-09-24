import pytest
from pydantic import ValidationError

from app.api.schemas import CandidateInputSchema, ScreeningRequestSchema
from app.stage1_rules.contracts import (
    AttributeSource, AuthorizationStatus, CandidateAttributes, CandidateInput,
    DegreeRequirement, HardFilterRules, resolve_hard_filters,
)
from app.stage1_rules.rules_engine import evaluate_stage1_hard_filters, parse_degree_entries


def evaluate(cv="", years=5, auth="eligible", rules=None, **kwargs):
    return evaluate_stage1_hard_filters(
        candidate_yoe=years, candidate_cv_text=cv, work_authorized=auth,
        jd_profile=rules or {}, experience_source=kwargs.get("experience_source", "recruiter_verified"),
        authorization_source=kwargs.get("authorization_source", "recruiter_verified"),
    )


@pytest.mark.parametrize("value", [-1, float("nan"), float("inf"), -float("inf"), "5", "nan", True, False])
def test_invalid_experience_rejected_everywhere(value):
    with pytest.raises(ValidationError):
        CandidateAttributes(experience_years=value)
    with pytest.raises(ValidationError):
        HardFilterRules(min_years_experience=value)
    with pytest.raises(ValidationError):
        evaluate(years=value)


@pytest.mark.parametrize("rules", [
    {"minimum_experience": 5}, {"min_years_experince": 5},
    {"require_work_authorization": "false"}, {"degree_requirement": {"level": "MYSTERY"}},
    {"degree_requirement": {"level": "NONE", "fields": ["CS"]}},
    {"degree_requirement": {"level": "BACHELOR", "fields": [" "]}},
    {"degree_requirement": {"level": "BACHELOR", "field": ["CS"]}},
    {"degree_requirement": {"level": "BACHELOR", "level_aliases": ["MBA"]}},
])
def test_bad_requirements_do_not_silently_disable_filters(rules):
    with pytest.raises(ValidationError):
        HardFilterRules.model_validate(rules)
    with pytest.raises(ValidationError):
        resolve_hard_filters({"title": "Engineer", "hard_filter_rules": rules})


def test_profile_rules_are_normalized_without_false_conflicts():
    rules = HardFilterRules(min_years_experience=2, degree_requirement={"level": "Bachelor"})
    assert resolve_hard_filters({"title": "Engineer", "hard_filter_rules": rules.model_dump()}, rules) == rules
    with pytest.raises(ValueError, match="Conflicting"):
        resolve_hard_filters({"min_years_experience": 5}, {"min_years_experience": 2})
    with pytest.raises(ValidationError):
        resolve_hard_filters({"min_years_experince": 5})


@pytest.mark.parametrize("auth,source,expected", [
    ("eligible", "recruiter_verified", "PASS"), ("ineligible", "recruiter_verified", "FAIL"),
    ("unknown", "recruiter_verified", "REVIEW"), (None, "unknown", "REVIEW"),
    ("eligible", "cv_extracted", "REVIEW"), ("ineligible", "cv_extracted", "REVIEW"),
    ("eligible", "unknown", "REVIEW"), (True, "recruiter_verified", "PASS"),
    (False, "recruiter_verified", "FAIL"),
])
def test_authorization_policy(auth, source, expected):
    assert evaluate(auth=auth, authorization_source=source)["status"] == expected
    assert evaluate(auth=auth, authorization_source=source, rules={"require_work_authorization": False})["status"] == "PASS"


@pytest.mark.parametrize("auth", ["true", "false", "yes", 0, 1, {}, []])
def test_malformed_authorization_rejected(auth):
    with pytest.raises(ValidationError):
        evaluate(auth=auth)


@pytest.mark.parametrize("years,source,expected", [
    (None, "unknown", "REVIEW"), (0, "recruiter_verified", "FAIL"),
    (5, "recruiter_verified", "PASS"), (6, "cv_extracted", "REVIEW"),
    (1, "cv_extracted", "REVIEW"), (6, "unknown", "REVIEW"),
])
def test_experience_policy(years, source, expected):
    result = evaluate(years=years, experience_source=source, rules={"min_years_experience": 5})
    assert result["status"] == expected
    assert result["metrics"]["experience_source"] == source
    assert evaluate(years=years, experience_source=source)["status"] == "PASS"


def test_unknown_defaults_and_explicit_unknown_does_not_use_nested_claim():
    candidate = CandidateInput(candidate_id="a", raw_cv_text="")
    assert candidate.rule_facts().work_authorized == AuthorizationStatus.UNKNOWN
    candidate = CandidateInput(candidate_id="a", raw_cv_text="", work_authorized="unknown",
                               parsed_attributes={"work_authorized": "eligible", "authorization_source": "recruiter_verified"})
    assert candidate.rule_facts().work_authorized == AuthorizationStatus.UNKNOWN


def test_recruiter_overrides_are_explicit_and_preserve_reported_values():
    data = {"candidate_id": "a", "raw_cv_text": "",
            "parsed_attributes": {"experience_years": 9, "experience_source": "cv_extracted", "work_authorized": "ineligible"},
            "recruiter_overrides": {"experience_years": 3, "work_authorized": "eligible"}}
    candidate = CandidateInputSchema.model_validate(data)
    facts = candidate.rule_facts()
    assert facts.experience_years == 3 and facts.work_authorized == AuthorizationStatus.ELIGIBLE
    assert facts.experience_source == facts.authorization_source == AttributeSource.RECRUITER_VERIFIED
    assert candidate.parsed_attributes.experience_years == 9
    assert CandidateInput.model_validate(candidate.model_dump(exclude_unset=True)).rule_facts() == facts


def test_conflicting_authorization_requires_override():
    with pytest.raises(ValidationError):
        CandidateInput(candidate_id="a", raw_cv_text="", work_authorized="eligible",
                       parsed_attributes={"work_authorized": "ineligible"})


@pytest.mark.parametrize("cv,level,fields,expected", [
    ("EDUCATION\nB.S. in Computer Science", "BACHELOR", ["Computer Science"], "PASS"),
    ("EDUCATION\nM.S. in Computer Science", "MASTER", ["Computer Science"], "PASS"),
    ("EDUCATION\n+2 - National School", "+2", [], "PASS"),
    ("EDUCATION\nPh.D. in Computer Science", "PHD", ["Computer Science"], "PASS"),
    ("EDUCATION\nBachelor of Science\nComputer Science\nExpected graduation 2027", "BACHELOR", ["Computer Science"], "REVIEW"),
    ("EDUCATION\nCurrently pursuing\nBachelor in Computer Science", "BACHELOR", ["Computer Science"], "REVIEW"),
    ("EDUCATION\nBachelor of Science", "BACHELOR", ["Computer Science"], "REVIEW"),
    ("EDUCATION\nBachelor of Fine Arts", "BACHELOR", ["Computer Science"], "FAIL"),
    ("EDUCATION\nHigh School Diploma\nEXPERIENCE\nScrum Master at University", "MASTER", [], "FAIL"),
    ("EDUCATION\nHigh School Diploma", "DIPLOMA", [], "FAIL"),
    ("EXPERIENCE\nScrum Master at University", "MASTER", [], "REVIEW"),
    ("EDUCATION: Bachelor of Computer Science", "BACHELOR", ["Computer Science"], "PASS"),
    ("EDUCATION\nMBA in Business Administration; Bachelor in Computer Science", "MASTER", ["Computer Science"], "REVIEW"),
    ("EDUCATION\nMBA in Business Administration\nBachelor in Computer Science", "MASTER", ["Computer Science"], "FAIL"),
    ("", "BACHELOR", [], "REVIEW"),
])
def test_education_scope_and_ambiguity(cv, level, fields, expected):
    result = evaluate(cv=cv, rules={"degree_requirement": {"level": level, "fields": fields}})
    assert result["status"] == expected, result


@pytest.mark.asyncio
async def test_direct_pipeline_validates_before_masking(monkeypatch):
    from app import orchestrator
    def forbidden(*args):
        pytest.fail("Invalid input must be rejected before processing")
    monkeypatch.setattr(orchestrator, "mask_pii_runtime_view", forbidden)
    with pytest.raises(ValidationError):
        await orchestrator.run_end_to_end_screening_pipeline(
            [{"candidate_id": "a", "raw_cv_text": "", "parsed_attributes": {"experience_years": float("nan")}}],
            {"title": "Engineer"})


@pytest.mark.asyncio
async def test_unverified_claims_route_to_review_without_retrieval(monkeypatch):
    from app import orchestrator
    monkeypatch.setattr(orchestrator, "mask_pii_runtime_view", lambda text: text)
    def forbidden(**kwargs):
        pytest.fail("Stage 1 review cannot reach Stage 2")
    monkeypatch.setattr(orchestrator, "extract_candidate_category_evidence", forbidden)
    result = await orchestrator.run_end_to_end_screening_pipeline(
        [{"candidate_id": "a", "raw_cv_text": "", "parsed_attributes": {
            "experience_years": 10, "experience_source": "cv_extracted"}}],
        {"title": "Engineer", "hard_filter_rules": {"min_years_experience": 5}})
    assert result["rejected_candidates"] == []
    assert result["metrics"]["stage1_review_required"] == 1
    details = result["review_candidates"][0]["filter_details"]
    assert {check["code"] for check in details["checks"]} >= {"EXPERIENCE_UNVERIFIED", "AUTHORIZATION_UNKNOWN"}
    assert details["input_provenance"]["reported_experience_years"] == 10
