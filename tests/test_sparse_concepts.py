import pytest

from app.stage2_retrieval.sparse import build_sparse_plan, normalize_lexical_v1
from app.stage2_retrieval.hybrid_search import execute_category_hybrid_search


@pytest.mark.parametrize("mention,alias,wrong", [
    ("C#", "C Sharp", "C++"), ("C++", "CPP", "C#"),
    (".NET", "dotnet", "internet"), ("Node.js", "NodeJS", "node"),
    ("Java", "Java", "JavaScript"),
])
def test_technical_identity_and_boundaries(mention, alias, wrong):
    plan = build_sparse_plan(mention, required_skills=[{"canonical": mention}])
    assert plan.coverage(alias)[0] == 1
    assert plan.coverage(wrong)[0] == 0
    assert plan.coverage("unrelated text")[0] == 0


def test_any_concept_retrieval_preserves_phrases_and_alternatives():
    plan = build_sparse_plan("Strong experience required with Java, Spring Boot and databases",
        required_skills=[{"canonical": "Java", "substitutes": ["C#"]},
                         {"canonical": "Spring Boot"}],
        preferred_skills=[{"canonical": "PostgreSQL", "aliases": ["Postgres"]}])
    assert plan.coverage("Used C Sharp")[0] == 1
    assert plan.coverage("Java Java Java")[0] == 1
    assert plan.coverage("Spring holiday and leather boot")[0] == 0
    assert plan.coverage("Spring Boot with Postgres PostgreSQL")[:2] == (1, 1)
    assert "spring <-> boot" in plan.tsquery
    assert "required" not in plan.tsquery


def test_no_requirement_and_education_isolation():
    skills = [{"canonical": "Java"}]
    assert not build_sparse_plan("No explicit requirement", "EDUCATION", skills).concepts
    plan = build_sparse_plan("Bachelor in Computer Science", "EDUCATION", skills,
                             degree_requirement={"level": "BACHELOR", "fields": ["Computer Science"]})
    assert plan.coverage("Bachelor in Computer Science")[0] == 2
    assert plan.coverage("Java") == (0, 0, 0)


def test_sparse_only_hit_survives_full_union_and_coverage_wins_over_repetition():
    plan = build_sparse_plan("Java Spring Boot", required_skills=[
        {"canonical": "Java"}, {"canonical": "Spring Boot"}])
    chunks = [
        {"chunk_id": "dense", "text": "Unrelated", "embedding": [1, 0]},
        {"chunk_id": "sparse", "text": "Java Spring Boot", "embedding": [0, 1]},
        {"chunk_id": "repeat", "text": "Java " * 50, "embedding": [0, 1]},
    ]
    results = execute_category_hybrid_search("Java Spring Boot", [1, 0], chunks,
                                             top_k=1, sparse_plan=plan)
    assert {item["chunk_id"] for item in results} == {"dense", "sparse"}
    sparse = next(item for item in results if item["chunk_id"] == "sparse")
    assert sparse["dense_rank"] is None and sparse["sparse_rank"] == 1
    assert sparse["sparse_required_coverage"] == 2


def test_query_syntax_cannot_be_injected_and_normalization_is_idempotent():
    plan = build_sparse_plan("", required_skills=[{"canonical": "x'); ! y & z"}])
    assert plan.tsquery == "(x <-> y <-> z)"
    text = normalize_lexical_v1("C# .NET Node.js JavaScript C++")
    assert normalize_lexical_v1(text) == text


def test_legacy_comma_lists_keep_unknown_compound_names_together():
    plan = build_sparse_plan("Required experience with Apache Kafka, Apache Spark or Spring Boot")
    assert plan.coverage("Apache Kafka")[2] == 1
    assert plan.coverage("Apache unrelated Kafka")[2] == 0
    assert plan.coverage("Spring holiday boot")[2] == 0
    assert "apache <-> kafka" in plan.tsquery
    assert "apache <-> spark" in plan.tsquery


def test_education_alternative_fields_count_as_one_concept():
    plan = build_sparse_plan("No explicit requirement", "EDUCATION",
        degree_requirement={"level": "BACHELOR", "fields": ["Computer Science", "Information Technology"],
                            "field_aliases": ["CS", "IT"]})
    assert plan.coverage("Bachelor in CS and Information Technology")[0] == 2
