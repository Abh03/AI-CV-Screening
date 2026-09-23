import json
import logging
from typing import Dict, Any, Optional
from google import genai
from google.genai import types

from app.config import settings
from app.stage3_evaluation.schemas import LLMEvaluationOutput

logger = logging.getLogger("cv_screening")


class LLMClientWrapper:
    """
    Unified LLM Client supporting Mock and Google AI Studio Gemini API providers.
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
        else:
            logger.info("Initialized Mock LLM Client.")

    async def generate_evaluation(self, prompt_text: str, candidate_id: str) -> Dict[str, Any]:
        """
        Sends evaluation prompt to configured LLM provider and returns raw dict structured as LLMEvaluationOutput.
        """
        if self.provider == "gemini":
            return await self._call_gemini(prompt_text, candidate_id)
        else:
            return self._call_mock(candidate_id)

    async def _call_gemini(self, prompt_text: str, candidate_id: str) -> Dict[str, Any]:
        try:
            # Enforce structured JSON output using LLMEvaluationOutput schema
            config = types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=LLMEvaluationOutput,
                temperature=0.1,  # Low temperature for deterministic scoring
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
            logger.error(f"Gemini API call failed for candidate {candidate_id}: {str(e)}")
            raise e

    def _call_mock(self, candidate_id: str) -> Dict[str, Any]:
        """Returns mock evaluation matching LLMEvaluationOutput schema."""
        return {
            "skills": {
                "score": 90.0,
                "rationale": "Strong backend skill set covering Python, FastAPI, and PostgreSQL.",
                "citations": ["Proficient in Python, FastAPI, PostgreSQL, Redis, and Docker."]
            },
            "experience": {
                "score": 85.0,
                "rationale": "5 years of experience building scalable API microservices.",
                "citations": ["Backend Software Engineer (2019-2024): Built high-throughput microservices in FastAPI."]
            },
            "projects": {
                "score": 80.0,
                "rationale": "Demonstrated hands-on projects involving high-throughput data processing.",
                "citations": ["Built high-throughput microservices in FastAPI."]
            },
            "education": {
                "score": 95.0,
                "rationale": "B.S. degree in Computer Science meets required technical education level.",
                "citations": ["B.S. in Computer Science, University of Technology (2019)."]
            },
            "flags": [],
            "executive_summary": "Candidate exceeds technical expectations with robust microservices experience and strong computer science foundation."
        }


llm_client = LLMClientWrapper()