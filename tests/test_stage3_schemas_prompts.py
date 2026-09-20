from app.stage3_evaluation.schemas import (
    LLMEvaluationOutput,
    CategoryAssessment,
    FlagDetail,
    FlagType,
    Severity,
    DecisionTier
)
from app.stage3_evaluation.prompts import build_stage3_user_prompt
from app.stage3_evaluation.evaluator import compute_deterministic_tier


def test_stage3_user_prompt_xml_tagging():
    jd_profile = {
        "title": "Senior Python Architect",
        "jd_category_queries": {
            "SKILLS": "Python FastAPI PostgreSQL",
            "EXPERIENCE": "5+ years backend leadership",
            "PROJECTS": "Distributed services",
            "EDUCATION": "BS Computer Science"
        }
    }

    mock_evidence = {
        "evidence_by_category": {
            "SKILLS": [{"text": "Python, FastAPI, PostgreSQL"}],
            "EXPERIENCE": [{"text": "Lead Backend Engineer for 6 years"}],
            "PROJECTS": [],
            "EDUCATION": [{"text": "BS Computer Science, 2019"}]
        }
    }

    prompt = build_stage3_user_prompt("cand_201", jd_profile, mock_evidence)

    assert '<evaluation_request candidate_id="cand_201">' in prompt
    assert '<snippet tag="SKILLS:1">' in prompt
    assert '<snippet tag="EXPERIENCE:1">' in prompt
    assert '<snippet tag="NONE">' in prompt  # Projects category empty fallback


def test_deterministic_tier_calculation_and_citation_verifier():
    mock_evidence = {
        "evidence_by_category": {
            "SKILLS": [{"text": "Python FastAPI"}],
            "EXPERIENCE": [{"text": "5 years backend"}],
            "PROJECTS": [{"text": "Payment gateway"}],
            "EDUCATION": [{"text": "BS CS"}]
        }
    }

    mock_llm_output = LLMEvaluationOutput(
        skills=CategoryAssessment(score=85.0, rationale="Strong fit", citations=["SKILLS:1"]),
        experience=CategoryAssessment(score=80.0, rationale="Solid exp", citations=["EXPERIENCE:1"]),
        projects=CategoryAssessment(score=70.0, rationale="Good proj", citations=["PROJECTS:1", "INVALID:99"]),
        education=CategoryAssessment(score=90.0, rationale="Degree matches", citations=["EDUCATION:1"]),
        flags=[
            FlagDetail(
                type=FlagType.MISSING_INFORMATION,
                severity=Severity.LOW,
                description="Graduation month not specified",
                citations=["EDUCATION:1"]
            )
        ],
        executive_summary="Highly qualified candidate."
    )

    result = compute_deterministic_tier("cand_201", mock_llm_output, mock_evidence)

    # Composite = (0.4*80) + (0.3*85) + (0.15*70) + (0.15*90) = 32 + 25.5 + 10.5 + 13.5 = 81.5
    assert result.composite_score == 81.50
    assert result.tier == DecisionTier.TIER_1

    # Citation Verification
    assert "SKILLS:1" in result.verified_citations
    assert "INVALID:99" in result.invalid_citations


def test_critical_flag_and_low_skill_tier_demotion():
    mock_evidence = {
        "evidence_by_category": {
            "SKILLS": [{"text": "Java basic"}],
            "EXPERIENCE": [{"text": "10 years experience"}],
            "PROJECTS": [],
            "EDUCATION": []
        }
    }

    # Low Skill Score -> Forced TIER_3
    llm_out_low_skill = LLMEvaluationOutput(
        skills=CategoryAssessment(score=35.0, rationale="Lacks required Python", citations=[]),
        experience=CategoryAssessment(score=90.0, rationale="High YOE", citations=[]),
        projects=CategoryAssessment(score=50.0, rationale="Basic", citations=[]),
        education=CategoryAssessment(score=50.0, rationale="Basic", citations=[]),
        flags=[],
        executive_summary="Lacks core skills."
    )

    res_low_skill = compute_deterministic_tier("cand_202", llm_out_low_skill, mock_evidence)
    assert res_low_skill.tier == DecisionTier.TIER_3

    # Critical Flag -> Demoted from TIER_1 to TIER_2 or TIER_3
    llm_out_flag = LLMEvaluationOutput(
        skills=CategoryAssessment(score=85.0, rationale="Good skills", citations=[]),
        experience=CategoryAssessment(score=85.0, rationale="Good exp", citations=[]),
        projects=CategoryAssessment(score=80.0, rationale="Good proj", citations=[]),
        education=CategoryAssessment(score=80.0, rationale="Good edu", citations=[]),
        flags=[
            FlagDetail(
                type=FlagType.DOCUMENTED_INCONSISTENCY,
                severity=Severity.HIGH,
                description="Overlapping full-time senior roles at two companies",
                citations=[]
            )
        ],
        executive_summary="Strong skills but inconsistent employment history."
    )

    res_flag = compute_deterministic_tier("cand_203", llm_out_flag, mock_evidence)
    assert res_flag.tier == DecisionTier.TIER_2  # Demoted due to HIGH flag