from app.stage1_rules.rules_engine import evaluate_stage1_hard_filters


def test_mba_trap_resolution():
    # Candidate has MBA (Master) and Bachelor in Computer Science. Job requires Bachelor in CS.
    cv_text = (
        "EDUCATION:\n"
        "Master of Business Administration (MBA) - Tribhuvan University, 2022\n"
        "Bachelor of Science in Computer Science - Kathmandu University, 2019\n"
    )

    jd_profile = {
        "min_years_experience": 2.0,
        "degree_requirement": {
            "level": "Bachelor",
            "fields": ["Computer Science"],
            "field_aliases": ["cs", "it"]
        }
    }

    result = evaluate_stage1_hard_filters(
        candidate_yoe=3.0,
        candidate_cv_text=cv_text,
        work_authorized=True,
        jd_profile=jd_profile
    )

    assert result["status"] == "PASS"
    assert len(result["failed_reasons"]) == 0


def test_scrum_master_false_positive_prevention():
    # Candidate lists 'Scrum Master' in experience, but only holds High School in Education.
    cv_text = (
        "PROFESSIONAL EXPERIENCE:\n"
        "Scrum Master & Agile Coach at Tech Corp (2020 - Present).\n\n"
        "EDUCATION:\n"
        "High School Diploma - National School, 2018"
    )

    jd_profile = {
        "min_years_experience": 1.0,
        "degree_requirement": {
            "level": "Master",
            "fields": ["Computer Science"],
            "field_aliases": []
        }
    }

    result = evaluate_stage1_hard_filters(
        candidate_yoe=4.0,
        candidate_cv_text=cv_text,
        work_authorized=True,
        jd_profile=jd_profile
    )

    assert result["status"] == "FAIL"
    assert any("Education level mismatch" in reason for reason in result["failed_reasons"])


def test_in_progress_degree_triggers_review():
    cv_text = (
        "EDUCATION:\n"
        "Bachelor in Information Technology - Expected Graduation 2026 (Pursuing)\n"
    )

    jd_profile = {
        "min_years_experience": 0.0,
        "degree_requirement": {
            "level": "Bachelor",
            "fields": ["Information Technology"],
            "field_aliases": ["it"]
        }
    }

    result = evaluate_stage1_hard_filters(
        candidate_yoe=1.0,
        candidate_cv_text=cv_text,
        work_authorized=True,
        jd_profile=jd_profile
    )

    assert result["status"] == "REVIEW"
    assert len(result["review_notes"]) == 1
    assert "IN-PROGRESS" in result["review_notes"][0]


def test_wrong_field_degree_fails():
    cv_text = (
        "EDUCATION:\n"
        "Bachelor of Fine Arts - Tribhuvan University, 2020\n"
    )

    jd_profile = {
        "min_years_experience": 1.0,
        "degree_requirement": {
            "level": "Bachelor",
            "fields": ["Computer Science", "Software Engineering"],
            "field_aliases": ["cs", "it"]
        }
    }

    result = evaluate_stage1_hard_filters(
        candidate_yoe=2.0,
        candidate_cv_text=cv_text,
        work_authorized=True,
        jd_profile=jd_profile
    )

    assert result["status"] == "FAIL"
    assert any("Education field mismatch" in reason for reason in result["failed_reasons"])