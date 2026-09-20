import pytest
from app.orchestrator import run_end_to_end_screening_pipeline
from app.stage3_evaluation.schemas import DecisionTier


@pytest.mark.asyncio
async def test_full_pipeline_end_to_end_execution():
    jd_profile = {
        "title": "Senior Python Backend Engineer",
        "jd_category_queries": {
            "SKILLS": "Python FastAPI PostgreSQL Redis Kubernetes",
            "EXPERIENCE": "Senior Backend Developer building microservices APIs",
            "PROJECTS": "High throughput distributed transaction processing",
            "EDUCATION": "Bachelor in Computer Science or Software Engineering"
        }
    }

    hard_filter_rules = {
        "min_years_experience": 3.0,
        "degree_requirement": {
            "level": "BACHELOR",
            "fields": [],
            "field_aliases": []
        }
    }

    raw_candidates = [
        # Candidate 1: Strong candidate (Passes Stage 1, High Stage 2/3 Score)
        {
            "candidate_id": "cand_001",
            "raw_cv_text": (
                "John Doe\nEmail: john@example.com\nPhone: +1-555-0199\n\n"
                "WORK EXPERIENCE\n"
                "Senior Backend Engineer at TechCorp (2020 - Present).\n"
                "Architected high-throughput FastAPI microservices backed by PostgreSQL and Redis.\n"
                "Managed Kubernetes clusters on AWS.\n\n"
                "TECHNICAL SKILLS\n"
                "Python, FastAPI, PostgreSQL, Redis, Kubernetes, AWS\n\n"
                "EDUCATION\n"
                "Bachelor of Science in Computer Engineering - TU, 2019\n"
            ),
            "parsed_attributes": {
                "experience_years": 5.0,
                "degree": "BACHELOR"
            }
        },
        # Candidate 2: Underqualified (Fails Stage 1 Hard Filters)
        {
            "candidate_id": "cand_002",
            "raw_cv_text": (
                "Jane Smith\nEmail: jane@example.com\n\n"
                "WORK EXPERIENCE\n"
                "Junior Support Tech (2023 - Present).\n"
                "Fixed office hardware and basic desktop issues.\n"
            ),
            "parsed_attributes": {
                "experience_years": 1.0,
                "degree": "HIGH_SCHOOL"
            }
        }
    ]

    result = await run_end_to_end_screening_pipeline(
        raw_candidates=raw_candidates,
        jd_profile=jd_profile,
        hard_filter_rules=hard_filter_rules,
        top_n_stage2_cutoff=10
    )

    metrics = result["metrics"]
    leaderboard = result["leaderboard"]
    rejected = result["rejected_candidates"]

    # Verify Pipeline Metrics
    assert metrics["total_input_candidates"] == 2
    assert metrics["stage0_processed"] == 2
    assert metrics["stage1_passed"] == 1
    assert metrics["stage1_rejected"] == 1
    assert metrics["stage2_shortlisted"] == 1
    assert metrics["stage3_evaluated"] == 1

    # Verify Leaderboard Content
    assert len(leaderboard) == 1
    top_cand = leaderboard[0]
    assert top_cand["candidate_id"] == "cand_001"
    assert top_cand["composite_score"] > 0.0
    assert top_cand["tier"] in [DecisionTier.TIER_1, DecisionTier.TIER_2]

    # Verify Stage 1 Rejections
    assert len(rejected) == 1
    assert rejected[0]["candidate_id"] == "cand_002"
    assert rejected[0]["reason"] == "FAILED_STAGE1_FILTERS"