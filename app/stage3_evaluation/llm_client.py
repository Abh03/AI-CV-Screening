import json
import logging
import asyncio
import time
import httpx
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
from pydantic import ValidationError
from google import genai
from google.genai import types

from app.config import settings
from app.stage3_evaluation.schemas import LLMEvaluationOutput, FinalCandidateEvaluation

logger = logging.getLogger("cv_screening")


class ProviderRateLimited(Exception):
    failure_code = 'PROVIDER_RATE_LIMITED'
    exhausted_code = 'PROVIDER_RATE_LIMIT_EXHAUSTED'
    def __init__(self, retry_after: float | None = None):
        super().__init__("Provider rate limited")
        self.retry_after = retry_after


class ProviderTransientFailure(Exception):
    failure_code = 'PROVIDER_TRANSIENT_FAILURE'
    exhausted_code = 'PROVIDER_TRANSIENT_RETRY_EXHAUSTED'

    def __init__(self, retry_after: float | None = None):
        super().__init__('Transient provider failure')
        self.retry_after = retry_after


def retry_after_seconds(value: str | None) -> float | None:
    if not value:
        return None
    if hasattr(value, "total_seconds"):
        value = value.total_seconds()
    try:
        seconds = float(value)
        if 0 <= seconds <= 86400:
            return seconds
    except (TypeError, ValueError):
        pass
    try:
        return max(0.0, min(86400.0,
            (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()))
    except (TypeError, ValueError, OverflowError):
        return None


class LLMClientWrapper:
    """
    Unified LLM Client supporting Gemini, Groq, OpenRouter, and Mock providers.
    """

    def __init__(self):
        self.provider = settings.LLM_PROVIDER.lower()
        self.gemini_client: Optional[genai.Client] = None
        self.http_client: Optional[httpx.AsyncClient] = None

        self._initialized = False

    async def aclose(self):
        if self.http_client is not None and not self.http_client.is_closed:
            await self.http_client.aclose()
        self.http_client = None
        if self.gemini_client is not None:
            close = getattr(self.gemini_client.aio, "aclose", None)
            if close is not None:
                await close()
            sync_close = getattr(self.gemini_client, "close", None)
            if sync_close is not None:
                sync_close()
            self.gemini_client = None
            self._initialized = False

    def _initialize(self):
        if self.provider == "mock" and settings.ENVIRONMENT.lower() not in {"development", "test", "testing"}:
            raise ValueError("Mock provider is restricted to development/test environments")
        if self._initialized:
            return
        if self.provider == "gemini":
            api_key = settings.GEMINI_API_KEY
            if not api_key or api_key == "mock_key_for_now":
                raise ValueError("GEMINI_API_KEY must be configured in .env when LLM_PROVIDER='gemini'")
            self.gemini_client = genai.Client(api_key=api_key,
                http_options=types.HttpOptions(retry_options=types.HttpRetryOptions(attempts=1)))
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

    async def generate_evaluation(self, *, system_prompt: str, user_prompt: str,
                                  candidate_id: str, max_provider_attempts: int = 3) -> Dict[str, Any]:
        self._initialize()
        if self.provider == "gemini":
            return await self._call_gemini(system_prompt, user_prompt, candidate_id, max_provider_attempts)
        elif self.provider == "groq":
            return await self._call_groq(system_prompt, user_prompt, candidate_id, max_provider_attempts)
        elif self.provider == "openrouter":
            return await self._call_openrouter(system_prompt, user_prompt, candidate_id, max_provider_attempts)
        else:
            return self._call_mock(candidate_id)

    async def _call_gemini(self, system_prompt: str, user_prompt: str, candidate_id: str,
                           max_provider_attempts: int = 3) -> Dict[str, Any]:
        for attempt in range(max_provider_attempts):
            try:
                started = time.monotonic()
                logger.info('Provider request', extra={'event':'provider_request',
                    'provider':'Gemini','model':settings.GEMINI_MODEL})
                config = types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    response_mime_type="application/json",
                    response_json_schema=LLMEvaluationOutput.model_json_schema(),
                    temperature=0.1,
                )

                response = await asyncio.wait_for(
                    self.gemini_client.aio.models.generate_content(
                        model=settings.GEMINI_MODEL, contents=user_prompt, config=config),
                    timeout=settings.PROVIDER_TIMEOUT_SECONDS)

                metrics = {'event':'provider_response','provider':'Gemini','model':settings.GEMINI_MODEL,
                    'status':200,'latency_ms':round((time.monotonic()-started)*1000,3)}
                usage = getattr(response,'usage_metadata',None)
                for source,target in (('prompt_token_count','input_tokens'),('total_token_count','total_tokens')):
                    value = getattr(usage,source,None)
                    if isinstance(value,int) and not isinstance(value,bool) and value >= 0:
                        metrics[target]=value
                candidate_tokens=getattr(usage,'candidates_token_count',None)
                thought_tokens=getattr(usage,'thoughts_token_count',None)
                if isinstance(candidate_tokens,int) and not isinstance(candidate_tokens,bool) and candidate_tokens >= 0:
                    metrics['output_tokens']=candidate_tokens+(thought_tokens if isinstance(thought_tokens,int) and thought_tokens>=0 else 0)
                resolved_model=getattr(response,'model_version',None)
                if isinstance(resolved_model,str) and len(resolved_model)<=200 and not any(char.isspace() for char in resolved_model):
                    metrics['resolved_model']=resolved_model
                logger.info('Provider response',extra=metrics)

                if not response.text:
                    raise ValueError("Empty response received from Gemini API.")

                return json.loads(response.text)

            except Exception as e:
                status_code = getattr(e, "code", None) or getattr(e, "status_code", None)
                # Successful HTTP responses were already recorded, even if their JSON is invalid.
                if not isinstance(e,(json.JSONDecodeError,ValueError)) or isinstance(e,asyncio.TimeoutError):
                    logger.info('Provider failure',extra={'event':'provider_response','provider':'Gemini',
                        'model':settings.GEMINI_MODEL,'status':status_code or ('TIMEOUT' if isinstance(e,asyncio.TimeoutError) else 'TRANSPORT_ERROR'),
                        'latency_ms':round((time.monotonic()-started)*1000,3)})
                if str(status_code) == "429":
                    headers = (getattr(e, "headers", None) or
                               getattr(getattr(e, "response", None), "headers", None) or {})
                    hint = (headers.get("retry-after") or headers.get("Retry-After") or
                            getattr(e, "retry_after", None) or getattr(e, "retry_delay", None))
                    raise ProviderRateLimited(retry_after_seconds(hint)) from e
                if (status_code in (500, 502, 503, 504) or isinstance(e, (TimeoutError, asyncio.TimeoutError))) and attempt < max_provider_attempts - 1:
                    wait_time = min(8, 2 ** attempt)
                    logger.warning(f"Gemini API rate limit/overload for {candidate_id}. Retrying in {wait_time}s...")
                    await asyncio.sleep(wait_time)
                else:
                    logger.error(f"Gemini API call failed ({type(e).__name__})")
                    if str(status_code) in {'500','502','503','504'} or isinstance(e,(TimeoutError,asyncio.TimeoutError)):
                        raise ProviderTransientFailure() from e
                    raise e

    async def _call_groq(self, system_prompt: str, user_prompt: str, candidate_id: str,
                         max_provider_attempts: int = 3) -> Dict[str, Any]:
        """Call Groq API with strict JSON schema enforcement."""
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {settings.GROQ_API_KEY}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": settings.GROQ_MODEL,
            "messages": [{"role": "system", "content": system_prompt},
                         {"role": "user", "content": user_prompt}],
            "temperature": 0.1,
            "response_format": self._get_structured_response_format(),
        }

        return await self._execute_openai_compatible_http(url, headers, payload, "Groq", candidate_id,
                                                          max_provider_attempts)

    async def _call_openrouter(self, system_prompt: str, user_prompt: str, candidate_id: str,
                               max_provider_attempts: int = 3) -> Dict[str, Any]:
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
            "max_tokens": settings.OPENROUTER_MAX_TOKENS,
            "messages": [{"role": "system", "content": system_prompt},
                         {"role": "user", "content": user_prompt}],
            "temperature": 0.1,
            "response_format": self._get_structured_response_format(),
            "provider": {
                "require_parameters": True
            },
        }

        return await self._execute_openai_compatible_http(url, headers, payload, "OpenRouter", candidate_id,
                                                          max_provider_attempts)

    async def _execute_openai_compatible_http(
        self, url: str, headers: Dict[str, str], payload: Dict[str, Any], provider_name: str,
        candidate_id: str, max_provider_attempts: int = 3
    ) -> Dict[str, Any]:
        if self.http_client is None or self.http_client.is_closed:
            self.http_client = httpx.AsyncClient(timeout=settings.PROVIDER_TIMEOUT_SECONDS)
        client = self.http_client
        for attempt in range(max_provider_attempts):
            try:
                started = time.monotonic()
                logger.info("Provider request", extra={"event": "provider_request",
                            "provider": provider_name, "model": payload.get("model")})
                try:
                    response = await asyncio.wait_for(
                        client.post(url, headers=headers, json=payload),
                        timeout=settings.PROVIDER_TIMEOUT_SECONDS)
                except (asyncio.TimeoutError, httpx.TimeoutException):
                    logger.info("Provider deadline", extra={"event":"provider_response",
                        "provider":provider_name,"model":payload.get('model'),"status":"TIMEOUT",
                        "error_code":"PROVIDER_TIMEOUT","latency_ms":round((time.monotonic()-started)*1000,3)})
                    raise
                metrics = {"event": "provider_response", "provider": provider_name,
                           "model": payload.get("model"), "status": response.status_code,
                           "latency_ms": round((time.monotonic() - started) * 1000, 3)}
                if response.status_code < 400:
                    try:
                        usage = response.json().get("usage", {})
                        resolved_model = response.json().get('model')
                        if isinstance(resolved_model,str) and len(resolved_model)<=200 and not any(char.isspace() for char in resolved_model):
                            metrics['resolved_model']=resolved_model
                        for source, target in (("prompt_tokens", "input_tokens"),
                                               ("completion_tokens", "output_tokens"),
                                               ("total_tokens", "total_tokens"), ("cost", "cost")):
                            value = usage.get(source)
                            if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
                                metrics[target] = value
                    except (ValueError, AttributeError, TypeError):
                        pass
                logger.info("Provider response", extra=metrics)
                if response.status_code == 429:
                    raise ProviderRateLimited(retry_after_seconds(response.headers.get("retry-after")))
                if response.status_code >= 400:
                    logger.error("%s HTTP %s", provider_name, response.status_code)
                    response.raise_for_status()
                data = response.json()
                content = data["choices"][0]["message"]["content"]
                return json.loads(self._clean_json_text(content))
            except Exception as exc:
                if isinstance(exc, ProviderRateLimited):
                    raise
                retryable = ((isinstance(exc, httpx.HTTPStatusError) and
                              exc.response.status_code in (429, 500, 502, 503, 504)) or
                             isinstance(exc, (httpx.TimeoutException, httpx.TransportError, asyncio.TimeoutError)))
                if retryable and attempt < max_provider_attempts - 1:
                    wait_time = min(8, 2 ** attempt)
                    logger.warning("%s transient failure for %s; retrying in %ss", provider_name,
                                   candidate_id, wait_time)
                    await asyncio.sleep(wait_time)
                else:
                    logger.error("%s API call failed (%s)", provider_name, type(exc).__name__)
                    if retryable:
                        headers = getattr(getattr(exc,'response',None),'headers',{})
                        raise ProviderTransientFailure(retry_after_seconds(headers.get('retry-after'))) from exc
                    raise

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
        if settings.ENVIRONMENT.lower() not in {"development", "test", "testing"}:
            raise ValueError("Mock provider is restricted to development/test environments")
        return {
            "skills": {
                "score": 90.0,
                "rationale": "Synthetic Mock output for offline testing; not an evidence judgment.",
                "citations": ["SKILLS:1"]
            },
            "experience": {
                "score": 85.0,
                "rationale": "Synthetic Mock output for offline testing; not an evidence judgment.",
                "citations": ["EXPERIENCE:1"]
            },
            "projects": {
                "score": 80.0,
                "rationale": "Synthetic Mock output for offline testing; not an evidence judgment.",
                "citations": ["PROJECTS:1"]
            },
            "education": {
                "score": 95.0,
                "rationale": "Synthetic Mock output for offline testing; not an evidence judgment.",
                "citations": ["EDUCATION:1"]
            },
            "flags": [],
            "executive_summary": "Synthetic Mock output for offline testing; not an evidence judgment."
        }


llm_client = LLMClientWrapper()

class MockLLMProvider:
    """Restored offline provider interface; synthetic scores are not evidence judgments."""

    is_mock = True

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
    raise_rate_limits: bool = False,
    max_provider_attempts: int = 3,
) -> FinalCandidateEvaluation:
    # Local import avoids the evaluator/client circular dependency.
    from app.stage3_evaluation.evaluator import compute_deterministic_tier
    from app.stage3_evaluation.scoring import failed_evaluation
    from app.stage3_evaluation.prompts import SYSTEM_PROMPT_STAGE3, build_stage3_user_prompt

    if max_retries < 0:
        raise ValueError("max_retries must be nonnegative")
    if max_provider_attempts < 1:
        raise ValueError("max_provider_attempts must be positive")
    candidate_id = candidate_payload.get("candidate_id", "UNKNOWN")
    from app.stage3_evaluation.evidence import build_evidence_registry
    from app.stage0_extraction.injection_guard import scan_for_injection_anomalies
    is_mock = llm_client.provider == "mock" if llm_provider is None else bool(getattr(llm_provider, "is_mock", False))
    if is_mock and settings.ENVIRONMENT.lower() not in {"development", "test", "testing"}:
        return failed_evaluation(candidate_id, "MOCK_NOT_ALLOWED", "Mock evaluation is disabled in this environment.", is_mock=True)
    try:
        registry = build_evidence_registry(candidate_id, candidate_payload)
        user_prompt = build_stage3_user_prompt(candidate_id, jd_profile, candidate_payload, registry=registry)
        jd_text = "\n".join([jd_profile.get("title", ""), *jd_profile.get("jd_category_queries", {}).values()])
        injection_signals = ["JD_INJECTION_SIGNAL"] if scan_for_injection_anomalies(jd_text)["is_flagged"] else []
        if scan_for_injection_anomalies(candidate_id)["is_flagged"]:
            injection_signals.append("IDENTIFIER_INJECTION_SIGNAL")
    except (ValueError, TypeError, AttributeError):
        return failed_evaluation(candidate_id, "INVALID_EVIDENCE", "Invalid evidence or prompt context.", is_mock=is_mock)
    for attempt in range(max_retries + 1):
        try:
            if llm_provider is None:
                # Honor configured real providers instead of silently using Mock.
                provider_options = ({"max_provider_attempts": max_provider_attempts}
                                    if max_provider_attempts != 3 else {})
                data = await llm_client.generate_evaluation(
                    system_prompt=SYSTEM_PROMPT_STAGE3, user_prompt=user_prompt,
                    candidate_id=candidate_id, **provider_options
                )
            else:
                raw_response = await llm_provider.generate_structured_evaluation(
                    system_prompt=SYSTEM_PROMPT_STAGE3, user_prompt=user_prompt
                )
                data = json.loads(raw_response)
            parsed_output = LLMEvaluationOutput.model_validate(data)
            return compute_deterministic_tier(
                candidate_id, parsed_output, candidate_payload, registry=registry,
                injection_signals=injection_signals, is_mock=is_mock
            )
        except (json.JSONDecodeError, ValidationError) as exc:
            if attempt == max_retries:
                return failed_evaluation(candidate_id, "INVALID_LLM_OUTPUT", "Evaluation failed: invalid provider response.", is_mock=is_mock)
        except ProviderTransientFailure:
            if raise_rate_limits:
                raise
            return failed_evaluation(candidate_id, 'PROVIDER_ERROR', 'Evaluation failed: provider request could not complete.', is_mock=is_mock)
        except ProviderRateLimited as exc:
            if raise_rate_limits:
                raise
            return failed_evaluation(candidate_id, "PROVIDER_RATE_LIMITED", "Provider rate limit; retry is required.", is_mock=is_mock)
        except Exception as exc:
            # Provider details may contain sensitive data; return a stable operational error.
            return failed_evaluation(candidate_id, "PROVIDER_ERROR", "Evaluation failed: provider request could not complete.", is_mock=is_mock)


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
    from app.stage3_evaluation.scoring import evaluation_sort_key
    return sorted(results, key=evaluation_sort_key)
