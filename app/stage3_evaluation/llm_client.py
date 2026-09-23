import json
import logging
import asyncio
import httpx
from typing import Dict, Any, Optional
from google import genai
from google.genai import types

from app.config import settings
from app.stage3_evaluation.schemas import LLMEvaluationOutput

logger = logging.getLogger("cv_screening")


class LLMClientWrapper:
    """
    Unified LLM Client supporting Gemini, Groq, OpenRouter, and Mock providers.
    """

    def __init__(self):
        self.provider = settings.LLM_PROVIDER.lower()
        self.gemini_client: Optional[genai.Client] = None

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

        else:
            logger.info("Initialized Mock LLM Client.")

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

    def _call_mock(self, candidate_id: str) -> Dict[str, Any]:
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