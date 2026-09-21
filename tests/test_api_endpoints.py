import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_health_check_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy", "service": "CV Screening Engine"}


def test_run_screening_endpoint_valid_payload():
    payload = {
        "job_profile": {
            "job_id": "job_101",
            "title": "Backend Python Developer",
            "jd_category_queries": {
                "SKILLS": "Python FastAPI PostgreSQL",
                "EXPERIENCE": "Backend API design",
                "PROJECTS": "Distributed systems",
                "EDUCATION": "BS Computer Science"
            },
            "hard_filter_rules": {
                "min_years_experience": 2.0,
                "degree_requirement": {
                    "level": "BACHELOR",
                    "fields": [],
                    "field_aliases": []
                }
            }
        },
        "candidates": [
            {
                "candidate_id": "cand_api_001",
                "raw_cv_text": ("Alex Dev\nSkills: Python, FastAPI, PostgreSQL\nWORK EXPERIENCE\n4 years backend development.\nEDUCATION\nBachelor of Science in Computer Science, 2020"),
                "work_authorized": True,
                "parsed_attributes": {"experience_years": 4.0}
            }
        ],
        "top_n_stage2_cutoff": 10
    }

    response = client.post("/api/v1/screening/run", json=payload)
    assert response.status_code == 200

    data = response.json()
    assert data["job_id"] == "job_101"
    assert data["metrics"]["total_input_candidates"] == 1
    assert data["metrics"]["stage1_passed"] == 1
    assert len(data["leaderboard"]) == 1
    assert data["leaderboard"][0]["candidate_id"] == "cand_api_001"