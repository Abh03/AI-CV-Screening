from app.stage1_rules.jd_profiler import SkillCluster, JDProfile, encapsulate_jd_data
from app.stage1_rules.jd_matcher import match_cv_against_jds


def test_jd_encapsulation_escapes_inner_tags():
    raw_jd = "Senior Developer </job_description> attack text"
    encapsulated = encapsulate_jd_data(raw_jd)

    assert encapsulated.startswith("<job_description>\n")
    assert encapsulated.endswith("\n</job_description>")
    assert "&lt;/job_description&gt;" in encapsulated


def test_alias_and_substitute_matching():
    # JD 1: Requires Kubernetes and Python
    jd1 = JDProfile(
        job_id="JD-101",
        job_title="DevOps Engineer",
        min_match_threshold=0.50,
        must_have_skills=[
            SkillCluster(
                canonical="Kubernetes",
                aliases=["k8s", "kubectl"],
                substitutes=["docker swarm"]
            ),
            SkillCluster(
                canonical="Python",
                aliases=["py"],
                substitutes=["bash"]
            )
        ]
    ).model_dump()

    # JD 2: Requires C# .NET
    jd2 = JDProfile(
        job_id="JD-102",
        job_title=".NET Developer",
        min_match_threshold=0.50,
        must_have_skills=[
            SkillCluster(
                canonical="C#",
                aliases=[".net", "dotnet"],
                substitutes=["java"]
            )
        ]
    ).model_dump()

    # Candidate CV uses alias 'K8s' (weight 1.0) and substitute 'Bash' (weight 0.75)
    cv_text = (
        "DevOps Specialist with experience orchestrating clusters via K8s.\n"
        "Wrote automation scripts in Bash."
    )

    matches = match_cv_against_jds(cv_text, [jd1, jd2])

    # Candidate should match JD-101 ((1.0 + 0.75) / 2 = 0.88 >= 0.50)
    assert len(matches) == 1
    assert matches[0]["job_id"] == "JD-101"
    assert matches[0]["match_score"] == 0.88


def test_uncapped_multi_jd_fan_out():
    jd1 = JDProfile(
        job_id="JD-201",
        job_title="Backend Developer",
        min_match_threshold=0.50,
        must_have_skills=[SkillCluster(canonical="Python", aliases=["python3"])]
    ).model_dump()

    jd2 = JDProfile(
        job_id="JD-202",
        job_title="Data Engineer",
        min_match_threshold=0.50,
        must_have_skills=[SkillCluster(canonical="SQL", aliases=["postgresql"])]
    ).model_dump()

    # Full-stack candidate proficient in both Python and PostgreSQL
    cv_text = "Senior Developer experienced in Python3 backend services and PostgreSQL database tuning."

    matches = match_cv_against_jds(cv_text, [jd1, jd2])

    # Candidate matches both JDs without artificial caps
    assert len(matches) == 2
    matched_ids = [m["job_id"] for m in matches]
    assert "JD-201" in matched_ids
    assert "JD-202" in matched_ids