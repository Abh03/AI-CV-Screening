import json
import logging
import asyncio
import httpx
from typing import Dict, Any, Optional, List
from pydantic import ValidationError
from google import genai
from google.genai import types

from app.config import settings
from app.stage3_evaluation.schemas import LLMEvaluationOutput, FinalCandidateEvaluation

logger = logging.getLogger("cv_screening")


class LLMClientWrapper:
    """
    Unified LLM Client supporting Gemini, Groq, OpenRouter, and Mock providers.
    """

    def __init__(self):
        self.provider = settings.LLM_PROVIDER.lower()
        self.gemini_client: Optional[genai.Client] = None

        self._initialized = False

    def _initialize(self):
        if self._initialized:
            return
        if self.provider == "gemini":
            api_key = settings.GEMINI_API_KEY
            if not api_key or api_key == "mock_key_for_now":
                raise ValueError("GEMINI_API_KEY must be configured in .env when LLM_PROVIDER='gemini'")
            self.gemini_client = genai.Client(api_key=api_key)
            logger.info("Initialized Gemini LLM Client via Google AI Studio.")

        elif self.provider == "groq":
            if not settings.GROQ_API_KEY:
                raise ValueError("GROQ_API_KEY must be configured in .env when LLM_PROVIDER='groq'")
            logger.info(f"Initialized Groq LLM Client (Model: {settings.GROQ_MODEL}).")

        elif self.provider == "openrouter":
            if not settings.OPENROUTER_API_KEY:
                raise ValueError("OPENROUTER_API_KEY must be configured in .env when LLM_PROVIDER='openrouter'")
            logger.info(f"Initialized OpenRouter LLM Client (Model: {settings.OPENROUTER_MODEL}).")

        elif self.provider == "mock":
            logger.info("Initialized Mock LLM Client.")
        else:
            raise ValueError(f"Unsupported LLM provider: {self.provider}")
        self._initialized = True

    def _get_structured_response_format(self) -> Dict[str, Any]:
        """Generates OpenAI-compatible structured outputs format from Pydantic schema."""
        return {
            "type": "json_schema",
            "json_schema": {
                "name": "llm_evaluation_output",
                "strict": True,
                "schema": LLMEvaluationOutput.model_json_schema(),
            },
        }

    async def generate_evaluation(self, prompt_text: str, candidate_id: str) -> Dict[str, Any]:
        self._initialize()
        if self.provider == "gemini":
            return await self._call_gemini(prompt_text, candidate_id)
        elif self.provider == "groq":
            return await self._call_groq(prompt_text, candidate_id)
        elif self.provider == "openrouter":
            return await self._call_openrouter(prompt_text, candidate_id)
        else:
            return self._call_mock(candidate_id)

    async def _call_gemini(self, prompt_text: str, candidate_id: str) -> Dict[str, Any]:
        max_retries = 3
        for attempt in range(max_retries):
            try:
                config = types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_json_schema=LLMEvaluationOutput.model_json_schema(),
                    temperature=0.1,
                )

                response = self.gemini_client.models.generate_content(
                    model="gemini-3.6-flash",
                    contents=prompt_text,
                    config=config,
                )

                if not response.text:
                    raise ValueError("Empty response received from Gemini API.")

                return json.loads(response.text)

            except Exception as e:
                if ("503" in str(e) or "429" in str(e)) and attempt < max_retries - 1:
                    wait_time = (attempt + 1) * 2
                    logger.warning(f"Gemini API rate limit/overload for {candidate_id}. Retrying in {wait_time}s...")
                    await asyncio.sleep(wait_time)
                else:
                    logger.error(f"Gemini API call failed for candidate {candidate_id}: {str(e)}")
                    raise e

    async def _call_groq(self, prompt_text: str, candidate_id: str) -> Dict[str, Any]:
        """Call Groq API with strict JSON schema enforcement."""
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {settings.GROQ_API_KEY}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": settings.GROQ_MODEL,
            "messages": [{"role": "user", "content": prompt_text}],
            "temperature": 0.1,
            "response_format": self._get_structured_response_format(),
        }

        return await self._execute_openai_compatible_http(url, headers, payload, "Groq", candidate_id)

    async def _call_openrouter(self, prompt_text: str, candidate_id: str) -> Dict[str, Any]:
        """Call OpenRouter API with strict JSON schema enforcement."""
        url = "https://openrouter.ai/api/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
            "HTTP-Referer": "http://localhost:8000",
            "X-Title": "AI-CV-Screening-Engine",
            "Content-Type": "application/json",
        }
        payload = {
            "model": settings.OPENROUTER_MODEL,
            "messages": [{"role": "user", "content": prompt_text}],
            "temperature": 0.1,
            "response_format": self._get_structured_response_format(),
            "provider": {
                "require_parameters": True
            },
        }

        return await self._execute_openai_compatible_http(url, headers, payload, "OpenRouter", candidate_id)

    async def _execute_openai_compatible_http(
        self, url: str, headers: Dict[str, str], payload: Dict[str, Any], provider_name: str, candidate_id: str
    ) -> Dict[str, Any]:
        max_retries = 3
        async with httpx.AsyncClient(timeout=45.0) as client:
            for attempt in range(max_retries):
                try:
                    response = await client.post(url, headers=headers, json=payload)
                    if response.status_code >= 400:
                        logger.error(
                            f"{provider_name} HTTP {response.status_code}: {response.text}"
                        )
                        response.raise_for_status()

                    data = response.json()
                    content = data["choices"][0]["message"]["content"]

                    cleaned_content = self._clean_json_text(content)
                    return json.loads(cleaned_content)

                except Exception as e:
                    if (isinstance(e, httpx.HTTPStatusError) and e.response.status_code in [429, 503]) and attempt < max_retries - 1:
                        wait_time = (attempt + 1) * 2
                        logger.warning(f"{provider_name} rate limit/503 for {candidate_id}. Retrying in {wait_time}s...")
                        await asyncio.sleep(wait_time)
                    else:
                        logger.error(f"{provider_name} API call failed for candidate {candidate_id}: {str(e)}")
                        raise e

    def _clean_json_text(self, text: str) -> str:
        text = text.strip()
        if text.startswith("```json"):
            text = text[7:]
        elif text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        return text.strip()

    @staticmethod
    def _call_mock(candidate_id: str) -> Dict[str, Any]:
        return {
            "skills": {
                "score": 90.0,
                "rationale": "Strong backend skill set covering Python, FastAPI, and PostgreSQL.",
                "citations": ["SKILLS:1"]
            },
            "experience": {
                "score": 85.0,
                "rationale": "5 years of experience building scalable API microservices.",
                "citations": ["EXPERIENCE:1"]
            },
            "projects": {
                "score": 80.0,
                "rationale": "Demonstrated hands-on projects involving high-throughput data processing.",
                "citations": ["PROJECTS:1"]
            },
            "education": {
                "score": 95.0,
                "rationale": "B.S. degree in Computer Science meets required technical education level.",
                "citations": ["EDUCATION:1"]
            },
            "flags": [],
            "executive_summary": "Candidate exceeds technical expectations with robust microservices experience."
        }


llm_client = LLMClientWrapper()

class MockLLMProvider:
    """Restored offline provider interface; synthetic scores are not evidence judgments."""

    async def generate_structured_evaluation(self, system_prompt: str, user_prompt: str) -> str:
        await asyncio.sleep(0.05)
        response = LLMClientWrapper._call_mock("mock")
        for category, score in zip(
            ("skills", "experience", "projects", "education"), (85.0, 80.0, 75.0, 90.0)
        ):
            response[category]["score"] = score
        response["executive_summary"] = "Synthetic offline evaluation."
        return json.dumps(response)


async def evaluate_single_candidate_async(
    candidate_payload: Dict[str, Any],
    jd_profile: Dict[str, Any],
    llm_provider: Optional[Any] = None,
    max_retries: int = 2,
) -> FinalCandidateEvaluation:
    # Local import avoids the evaluator/client circular dependency.
    from app.stage3_evaluation.evaluator import compute_deterministic_tier, _fallback_evaluation
    from app.stage3_evaluation.prompts import SYSTEM_PROMPT_STAGE3, build_stage3_user_prompt

    if max_retries < 0:
        raise ValueError("max_retries must be nonnegative")
    candidate_id = candidate_payload.get("candidate_id", "UNKNOWN")
    user_prompt = build_stage3_user_prompt(candidate_id, jd_profile, candidate_payload)
    for attempt in range(max_retries + 1):
        try:
            if llm_provider is None:
                # Honor configured real providers instead of silently using Mock.
                data = await llm_client.generate_evaluation(
                    f"{SYSTEM_PROMPT_STAGE3}\n\n{user_prompt}", candidate_id
                )
            else:
                raw_response = await llm_provider.generate_structured_evaluation(
                    system_prompt=SYSTEM_PROMPT_STAGE3, user_prompt=user_prompt
                )
                data = json.loads(raw_response)
            parsed_output = LLMEvaluationOutput.model_validate(data)
            return compute_deterministic_tier(
                candidate_id, parsed_output, candidate_payload, user_prompt=user_prompt
            )
        except (json.JSONDecodeError, ValidationError) as exc:
            if attempt == max_retries:
                return _fallback_evaluation(candidate_id, f"Evaluation failed due to response parsing errors: {exc}")
        except Exception as exc:
            # Preserve current failure behavior until the Phase 2 outcome contract.
            return _fallback_evaluation(candidate_id, f"Provider error: {exc}")


async def evaluate_candidate_batch_async(
    candidate_payloads: List[Dict[str, Any]],
    jd_profile: Dict[str, Any],
    llm_provider: Optional[Any] = None,
    concurrency_limit: int = 5,
) -> List[FinalCandidateEvaluation]:
    if concurrency_limit < 1:
        raise ValueError("concurrency_limit must be positive")
    semaphore = asyncio.Semaphore(concurrency_limit)

    async def sema_eval(payload):
        async with semaphore:
            return await evaluate_single_candidate_async(payload, jd_profile, llm_provider)

    results = await asyncio.gather(*(sema_eval(payload) for payload in candidate_payloads))
    return sorted(results, key=lambda result: result.composite_score, reverse=True)
