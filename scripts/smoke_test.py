import sys
import time
import requests

BASE_URL = "http://localhost:8000"


def test_health():
    print("[1/2] Testing Health Endpoint...")
    try:
        response = requests.get(f"{BASE_URL}/health", timeout=10)
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        print("  ✓ Health Check Passed:", data)
    except Exception as e:
        print(f"  ✗ Health Check Failed: {e}")
        sys.exit(1)


def test_screening_pipeline():
    print("\n[2/2] Testing Screening Engine Endpoint...")
    payload = {
        "job_profile": {
            "job_id": "job_smoke_001",
            "title": "Lead Python Developer",
            "jd_category_queries": {
                "SKILLS": "Python FastAPI PostgreSQL Docker",
                "EXPERIENCE": "Backend Microservices Async Architecture",
                "PROJECTS": "High Throughput Screening Engine",
                "EDUCATION": "BS Computer Science"
            },
            "hard_filter_rules": {
                "min_years_experience": 3.0,
                "degree_requirement": {
                    "level": "BACHELOR",
                    "fields": [],
                    "field_aliases": []
                }
            }
        },
        "candidates": [
            {
                "candidate_id": "cand_smoke_101",
                "raw_cv_text": "John Smoke\nSkills: Python, FastAPI, Docker, PostgreSQL\nWORK EXPERIENCE\n5 years as backend developer building microservices.\nEDUCATION\nBachelor of Science in Computer Science",
                "work_authorized": True,
                "parsed_attributes": {"experience_years": 5.0}
            }
        ],
        "top_n_stage2_cutoff": 5
    }

    start_time = time.time()
    try:
        response = requests.post(f"{BASE_URL}/api/v1/screening/run", json=payload, timeout=60)
        elapsed = time.time() - start_time
        assert response.status_code == 200
        data = response.json()
        assert data["job_id"] == "job_smoke_001"
        assert len(data["leaderboard"]) == 1
        print(f"  ✓ Pipeline Run Passed in {elapsed:.2f}s!")
        print("  Leaderboard Summary:", data["leaderboard"][0])
    except Exception as e:
        print(f"  ✗ Pipeline Run Failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    print("=== Automated CV Engine Docker Smoke Test ===")
    test_health()
    test_screening_pipeline()
    print("\n✓ All production smoke tests passed successfully!")