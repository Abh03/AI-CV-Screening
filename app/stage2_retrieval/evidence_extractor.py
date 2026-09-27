from typing import List, Dict, Any
import asyncio
from app.stage2_retrieval.chunker import generate_cv_chunks
from app.stage2_retrieval.embeddings import generate_embeddings, generate_single_embedding
from app.stage2_retrieval.hybrid_search import execute_category_hybrid_search
from app.stage2_retrieval.reranker import rerank_category_chunks
from app.stage2_retrieval.sparse import (build_sparse_plan, retrieval_diagnostics, SparsePlan,
                                        SparseConcept, normalize_lexical_v1)
from app.stage1_rules.jd_matcher import identity_terms
from app.stage2_retrieval.coverage import (resolve_targets, score_coverage, candidate_sort_key,
                                         eligible, MAX_CANDIDATES_PER_JD)

DEFAULT_CATEGORY_WEIGHTS: Dict[str, float] = {
    "EXPERIENCE": 0.40,
    "SKILLS": 0.30,
    "PROJECTS": 0.15,
    "EDUCATION": 0.15
}


def retrieval_targets(queries, targets):
    resolved = {category: list(dict.fromkeys(target["text"] for target in targets
                                             if target["category"] == category))
                for category in DEFAULT_CATEGORY_WEIGHTS}
    # Derived project guidance remains useful even when projects are not scored.
    for category, query in queries.items():
        if not resolved.get(category) and query.strip() and "no explicit requirement" not in query.casefold():
            resolved[category] = [query]
    # Stage 3 still evaluates education/certification facts independently. Keep
    # their CV context available without inventing a Stage 2 education target.
    if not resolved["EDUCATION"]:
        resolved["EDUCATION"] = ["Education and certifications stated in the CV"]
    return resolved


def source_categories(category):
    return (["SKILLS", "EXPERIENCE", "PROJECTS"] if category == "SKILLS" else
            [category, "PROJECTS" if category == "EXPERIENCE" else "EXPERIENCE"]
            if category in ("EXPERIENCE", "PROJECTS") else [category])


def target_sparse_plan(query, category, targets, degree_requirement=None):
    plan = build_sparse_plan(query, category, degree_requirement=degree_requirement)
    concepts = list(plan.concepts)
    for target in targets:
        if target["category"] != category or target["text"] != query:
            continue
        for index, terms in enumerate(target["evidence_terms"]):
            alternatives = tuple(dict.fromkeys(normalize_lexical_v1(term) for term in identity_terms(terms)))
            alternatives = tuple(term for term in alternatives if term)
            if alternatives:
                concepts.append(SparseConcept(f'{target["target_id"]}:{index}', alternatives,
                    "preferred" if target["treatment"] == "preference" else "required"))
    return SparsePlan(tuple(concepts[:128]))


def retain_evidence(existing, hits, ranked, targets):
    """Retain reranked passages plus best lexical coverage for every target."""
    from app.stage2_retrieval.coverage import assess_target
    selected = list(ranked)
    for target in targets:
        if hits:
            best = max(hits, key=lambda hit: assess_target(target, [hit])["coverage"])
            if assess_target(target, [best])["coverage"] > 0:
                selected.append(best)
    known = {item["chunk_id"] for item in existing}
    for item in selected:
        if item["chunk_id"] not in known:
            item = dict(item)
            item.pop("embedding", None)
            existing.append(item)
            known.add(item["chunk_id"])
    return existing


def resolve_retrieval_queries(jd_category_queries: Dict[str, str],
                              required_skills: list | None = None) -> Dict[str, str]:
    """Derive a retrieval target without adding requirements to the JD."""
    queries = dict(jd_category_queries)
    project_query = queries.get("PROJECTS", "")
    if project_query.strip() and "no explicit requirement" not in project_query.casefold():
        return queries
    skills = []
    for skill in required_skills or []:
        name = skill.get("canonical", "") if isinstance(skill, dict) else getattr(skill, "canonical", "")
        if name.strip() and name.strip() not in skills:
            skills.append(name.strip())
    queries["PROJECTS"] = "Projects demonstrating hands-on experience"
    if skills:
        queries["PROJECTS"] += " with " + ", ".join(skills)
    return queries


def extract_candidate_category_evidence(
    candidate_id: str,
    redacted_cv_text: str,
    jd_category_queries: Dict[str, str],
    weights: Dict[str, float] = DEFAULT_CATEGORY_WEIGHTS,
    source_pages: list[dict] | None = None,
    required_skills: list | None = None,
    preferred_skills: list | None = None,
    degree_requirement=None,
    relevance_contract=None
) -> Dict[str, Any]:
    """
    Processes a single candidate CV:
    1. Structure-aware chunking.
    2. Target-specific Hybrid Retrieval (Dense + Sparse RRF).
    3. Category Cross-Encoder re-ranking.
    4. Supported relevance coverage with applicable-category weights.
    """
    if not redacted_cv_text:
        return {"candidate_id": candidate_id, "composite_score": 0.0, "evidence_by_category": {}, "status": "EMPTY_CV"}

    chunks = generate_cv_chunks(redacted_cv_text, source_pages=source_pages)
    if not chunks:
        return {"candidate_id": candidate_id, "composite_score": 0.0, "evidence_by_category": {}, "status": "NO_CHUNKS"}

    # Stable content-derived identities until database document/chunk IDs arrive.
    from app.stage3_evaluation.evidence import stable_identity
    document_id = "unredacted:" + stable_identity(candidate_id, redacted_cv_text)
    for chunk in chunks:
        chunk["candidate_id"] = candidate_id
        chunk["document_id"] = document_id
        chunk["chunk_id"] = "chunk:" + stable_identity(document_id, chunk["global_chunk_id"], chunk["text"])
        chunk.setdefault("source_location", {"section": chunk["section"], "chunk_index": chunk["chunk_index"]})

    # Generate Embeddings
    embeddings = generate_embeddings([c["text"] for c in chunks])
    for chunk, emb in zip(chunks, embeddings):
        chunk["embedding"] = emb

    targets, minimum = resolve_targets(jd_category_queries, required_skills, preferred_skills,
                                      degree_requirement, relevance_contract)
    jd_category_queries = resolve_retrieval_queries(jd_category_queries, required_skills)
    query_targets = retrieval_targets(jd_category_queries, targets)
    evidence_by_category: Dict[str, List[Dict[str, Any]]] = {}
    retrieval_by_category = {}

    for category in ["SKILLS", "EXPERIENCE", "PROJECTS", "EDUCATION"]:
        cat_queries = query_targets.get(category, [])
        scopes = source_categories(category)
        cat_chunks = [c for c in chunks if c.get("category") in scopes]

        if not cat_queries or not cat_chunks:
            evidence_by_category[category] = []
            continue

        evidence_by_category[category] = []
        retrieval_by_category[category] = []
        for cat_query in cat_queries:
            query_vector = generate_single_embedding(cat_query)
            # Each focused query has its own lexical plan; avoid whole-list dilution.
            plan = target_sparse_plan(cat_query, category, targets, degree_requirement)
            rrf_hits = execute_category_hybrid_search(query_text=cat_query, query_vector=query_vector,
                category_chunks=cat_chunks, top_k=10, sparse_plan=plan)
            top_ranked = rerank_category_chunks(cat_query, rrf_hits, top_n=2)
            retain_evidence(evidence_by_category[category], rrf_hits, top_ranked,
                            [target for target in targets if target["category"] == category])
            retrieval_by_category[category].append({"query": cat_query,
                **retrieval_diagnostics(plan, rrf_hits, top_ranked)})

    coverage = score_coverage(targets, evidence_by_category, weights, minimum)

    return {
        "candidate_id": candidate_id,
        **coverage,
        "relevance_targets": targets,
        "retrieval_by_category": retrieval_by_category,
        "evidence_by_category": evidence_by_category,
        "status": "SUCCESS"
    }


async def extract_candidate_category_evidence_postgres(
    candidate_id: str, redacted_cv_text: str, jd_category_queries: Dict[str, str],
    job_id: str, weights: Dict[str, float] = DEFAULT_CATEGORY_WEIGHTS,
    source_pages: list[dict] | None = None,
    required_skills: list | None = None,
    preferred_skills: list | None = None,
    degree_requirement=None,
    relevance_contract=None
) -> Dict[str, Any]:
    """Persist full-text chunks and retrieve both branches in PostgreSQL.

    redacted_cv_text is a legacy parameter name; screening passes unredacted text.
    """
    from app.models.database import AsyncSessionLocal
    from app.stage2_retrieval.repository import PostgresRetrievalRepository

    if not redacted_cv_text:
        return {"candidate_id": candidate_id, "composite_score": 0.0,
                "evidence_by_category": {}, "status": "EMPTY_CV"}
    chunks = generate_cv_chunks(redacted_cv_text, source_pages=source_pages)
    if not chunks:
        return {"candidate_id": candidate_id, "composite_score": 0.0,
                "evidence_by_category": {}, "status": "NO_CHUNKS"}
    targets, minimum = resolve_targets(jd_category_queries, required_skills, preferred_skills,
                                      degree_requirement, relevance_contract)
    jd_category_queries = resolve_retrieval_queries(jd_category_queries, required_skills)
    query_targets = retrieval_targets(jd_category_queries, targets)
    evidence_by_category = {}
    retrieval_by_category = {}
    async with AsyncSessionLocal() as session:
        repo = PostgresRetrievalRepository(session)
        document_id = await repo.prepare_document(candidate_id, redacted_cv_text,
                                                  chunks, source_pages)
        for category in ["SKILLS", "EXPERIENCE", "PROJECTS", "EDUCATION"]:
            queries = query_targets.get(category, [])
            scopes = source_categories(category)
            if not queries or not any(chunk["category"] in scopes for chunk in chunks):
                evidence_by_category[category] = []
                continue
            evidence_by_category[category] = []
            retrieval_by_category[category] = []
            for query in queries:
                query_vector = await repo.query_vector(job_id, category, query)
                plan = target_sparse_plan(query, category, targets, degree_requirement)
                hits = []
                for scope in scopes:
                    hits.extend(await repo.search(candidate_id, document_id, scope, query,
                        query_vector, fallback_to_experience=False, sparse_plan=plan))
                hits = list({hit["chunk_id"]: hit for hit in hits}.values())
                await session.commit()
                ranked = await asyncio.to_thread(rerank_category_chunks, query, hits, top_n=2)
                retain_evidence(evidence_by_category[category], hits, ranked,
                                [target for target in targets if target["category"] == category])
                retrieval_by_category[category].append({"query": query,
                    **retrieval_diagnostics(plan, hits, ranked)})
        await session.commit()
    coverage = score_coverage(targets, evidence_by_category, weights, minimum)
    return {"candidate_id": candidate_id, **coverage, "relevance_targets": targets,
            "retrieval_by_category": retrieval_by_category,
            "evidence_by_category": evidence_by_category,
            "status": "SUCCESS"}


def rank_and_filter_candidate_batch(
    candidate_payloads: List[Dict[str, Any]],
    top_n_llm: int = 15
) -> List[Dict[str, Any]]:
    """
    Select supported relevance only, up to the per-JD maximum of 15.
    """
    if not 1 <= top_n_llm <= MAX_CANDIDATES_PER_JD:
        raise ValueError("Stage 2 cutoff must be between 1 and 15")
    sorted_candidates = sorted(candidate_payloads, key=candidate_sort_key)
    return [item for item in sorted_candidates if eligible(item)][:top_n_llm]


def format_category_evidence_for_prompt(candidate_payload: Dict[str, Any]) -> str:
    """
    Formats category-isolated evidence chunks into XML structure for Stage 3 LLM prompts.
    """
    from xml.etree import ElementTree as ET
    from app.stage3_evaluation.prompts import build_stage3_user_prompt
    prompt = build_stage3_user_prompt(candidate_payload.get("candidate_id", "UNKNOWN"), {}, candidate_payload)
    evidence = ET.fromstring(prompt).find("candidate_evidence")
    evidence.set("candidate_id", candidate_payload.get("candidate_id", "UNKNOWN"))
    return ET.tostring(evidence, encoding="unicode")
