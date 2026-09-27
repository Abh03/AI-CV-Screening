import pytest

from app.stage1_rules.rules_engine import evaluate_stage1_hard_filters
from app.stage1_rules.experience import extract_experience


@pytest.mark.parametrize('term', ['REST API', 'REST APIs', 'RESTful API', 'RESTful APIs'])
def test_rest_api_identity_variants_satisfy_required_plural(term):
    from app.stage1_rules.jd_matcher import required_skill_evidence
    assert required_skill_evidence('Built services using ' + term,
                                   {'canonical': 'REST APIs'})['match_weight'] == 1


def evaluate(text, skills=None, years=None):
    return evaluate_stage1_hard_filters(years, text, None, {
        "require_work_authorization": False, "min_years_experience": 5,
        "must_have_skills": skills or [],
    })


@pytest.mark.parametrize("text", ["Total experience: 2 years", "2 years of professional experience"])
def test_extracted_shortfall_rejects_with_original_provenance(text):
    result = evaluate(text)
    assert result["status"] == "FAIL"
    assert result["metrics"]["candidate_yoe"] == 2
    assert result["metrics"]["experience_source"] == "cv_extracted"
    assert result["metrics"]["experience_evidence"]["excerpt"] == text
    assert any(check["code"] == "INSUFFICIENT_EXPERIENCE" for check in result["checks"])


@pytest.mark.parametrize("text", [
    "2 years of Python experience", "2 years of professional experience with Python",
    "Over 2 years of professional experience", "2+ years of professional experience",
    "Total experience: 2 years\nOverall experience: 7 years", "2019-2024 Developer",
])
def test_ambiguous_or_skill_specific_experience_is_not_total(text):
    assert extract_experience(text) is None
    assert evaluate(text)["status"] == "REVIEW"


@pytest.mark.parametrize("canonical,text", [
    ("JavaScript", "JS"), ("Kubernetes", "K8s"), ("Node.js", "NODE JS"),
    ("PostgreSQL", "Postgres"), ("C#", "C Sharp"), ("C++", "C++"),
    ("Machine Learning", "Machine-Learning"), ("Machine Learning", "Machine\nLearning"),
    (".NET", "dotnet"), ("Python", "Ｐｙｔｈｏｎ"),
])
def test_identity_variants_satisfy_mandatory_skills(canonical, text):
    result = evaluate(text, [{"canonical": canonical}], years=5)
    assert result["checks"][-1]["status"] == "PASS"
    assert result["status"] != "FAIL"


@pytest.mark.parametrize("canonical,text", [("Java", "JavaScript"), ("C++", "C"),
    (".NET", "net"), ("SQL", "No SQL experience"), ("Kubernetes", "kubectl")])
def test_different_or_negated_skills_fail(canonical, text):
    result = evaluate(text, [{"canonical": canonical}], years=5)
    assert result["status"] == "FAIL"
    assert result["checks"][-1]["code"] == "MISSING_REQUIRED_SKILLS"


def test_each_mandatory_group_is_required_and_explicit_substitute_is_accepted():
    skills = [{"canonical": "Python", "substitutes": ["Java"]}, {"canonical": "SQL"}]
    assert evaluate("Java and SQL", skills, years=5)["status"] != "FAIL"
    result = evaluate("Java", skills, years=5)
    assert result["status"] == "FAIL"
    assert result["checks"][-1]["canonical"] == "SQL"


def test_jd_schema_requires_terms_for_every_cluster():
    from app.jd_intake import extraction_json_schema, SYSTEM
    schema = extraction_json_schema()
    cluster = schema["$defs"]["SkillCluster"]
    assert set(cluster["required"]) == {"canonical", "aliases", "substitutes"}
    assert cluster["additionalProperties"] is False
    assert "SAME skill" in SYSTEM and "explicitly accepted" in SYSTEM
