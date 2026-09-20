import json
import asyncio
from typing import List, Dict, Any, Optional
from pydantic import ValidationError

from app.stage3_evaluation.schemas import (
    LLMEvaluationOutput,
    FinalCandidateEvaluation,
    CategoryAssessment,
    DecisionTier
)
from app.stage3_evaluation.prompts import (
    SYSTEM_PROMPT_STAGE3,
    build_stage3_user_prompt
)
from app.stage3_evaluation.evaluator import compute_deterministic_tier


class MockLLMProvider:
    """
    Default mock LLM provider for testing and offline development environments.
    Simulates a structured JSON response from an OpenAI/vLLM completion model.
    """
    async def generate_structured_evaluation(
        self,
        system_prompt: str,
        user_prompt: str
    ) -> str:
        await asyncio.sleep(0.05)  # Simulate network latency
        
        # Generates realistic mock JSON evaluation
        mock_response = {
            "skills": {
                "score": 85.0,
                "rationale": "Demonstrates strong alignment with required technical stack.",
                "citations": ["SKILLS:1"]
            },
            "experience": {
                "score": 80.0,
                "rationale": "Relevant backend engineering experience with microservices.",
                "citations": ["EXPERIENCE:1"]
            },
            "projects": {
                "score": 75.0,
                "rationale": "Project history aligns well with architectural requirements.",
                "citations": ["PROJECTS:1"]
            },
            "education": {
                "score": 90.0,
                "rationale": "Degree and academic background match specifications.",
                "citations": ["EDUCATION:1"]
            },
            "flags": [],
            "executive_summary": "Solid candidate demonstrating core requirements across all categories."
        }
        return json.dumps(mock_response)


async def evaluate_single_candidate_async(
    candidate_payload: Dict[str, Any],
    jd_profile: Dict[str, Any],
    llm_provider: Optional[Any] = None,
    max_retries: int = 2
) -> FinalCandidateEvaluation:
    """
    Evaluates a single candidate through the LLM pipeline:
    1. Builds structured XML user prompt.
    2. Sends request to LLM with retry alogic.
    3. Validates response against LLMEvaluationOutput schema.
    4. Runs Python deterministic tier computation and citation verification.
    """
    candidate_id = candidate_payload.get("candidate_id", "UNKNOWN")
    user_prompt = build_stage3_user_prompt(candidate_id, jd_profile, candidate_payload)
    
    if llm_provider is None:
        llm_provider = MockLLMProvider()

    raw_response = None
    parsed_output = None

    for attempt in range(max_retries + 1):
        try:
            raw_response = await llm_provider.generate_structured_evaluation(
                system_prompt=SYSTEM_PROMPT_STAGE3,
                user_prompt=user_prompt
            )
            data = json.loads(raw_response)
            parsed_output = LLMEvaluationOutput(**data)
            break
        except (json.JSONDecodeError, ValidationError) as e:
            if attempt == max_retries:
                # Fallback schema output if LLM repeatedly fails JSON structure
                parsed_output = LLMEvaluationOutput(
                    skills=CategoryAssessment(score=0.0, rationale="LLM evaluation failed.", citations=[]),
                    experience=CategoryAssessment(score=0.0, rationale="LLM evaluation failed.", citations=[]),
                    projects=CategoryAssessment(score=0.0, rationale="LLM evaluation failed.", citations=[]),
                    education=CategoryAssessment(score=0.0, rationale="LLM evaluation failed.", citations=[]),
                    flags=[],
                    executive_summary=f"Evaluation failed due to response parsing errors: {str(e)}"
                )

    return compute_deterministic_tier(
        candidate_id=candidate_id,
        llm_output=parsed_output,
        evidence_payload=candidate_payload
    )


async def evaluate_candidate_batch_async(
    candidate_payloads: List[Dict[str, Any]],
    jd_profile: Dict[str, Any],
    llm_provider: Optional[Any] = None,
    concurrency_limit: int = 5
) -> List[FinalCandidateEvaluation]:
    """
    Concurrently evaluates the top Stage 2 candidates using an asyncio semaphore limit.
    Returns candidates sorted by final composite score.
    """
    semaphore = asyncio.Semaphore(concurrency_limit)

    async def sema_eval(payload: Dict[str, Any]) -> FinalCandidateEvaluation:
        async with semaphore:
            return await evaluate_single_candidate_async(
                candidate_payload=payload,
                jd_profile=jd_profile,
                llm_provider=llm_provider
            )

    tasks = [sema_eval(p) for p in candidate_payloads]
    results = await asyncio.gather(*tasks)

    # Sort final evaluations descending by composite score
    results_list = list(results)
    results_list.sort(key=lambda x: x.composite_score, reverse=True)

    return results_list