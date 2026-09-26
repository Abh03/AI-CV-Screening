import asyncio
import logging
from types import SimpleNamespace
import pytest
from app.stage3_evaluation.llm_client import LLMClientWrapper
from app.stage3_evaluation.llm_client import ProviderRateLimited, ProviderTransientFailure
from app.config import settings


@pytest.mark.asyncio
async def test_total_deadline_cancels_a_request_that_never_finishes(monkeypatch):
    cancelled=[]
    class Client:
        is_closed=False
        async def post(self,*args,**kwargs):
            try:
                # An upstream may keep sending activity without finishing its body.
                while True:
                    await asyncio.sleep(.001)
            finally:
                cancelled.append(True)
    wrapper=LLMClientWrapper()
    wrapper.http_client=Client()
    monkeypatch.setattr(settings,'PROVIDER_TIMEOUT_SECONDS',.01)
    with pytest.raises(ProviderTransientFailure):
        await wrapper._execute_openai_compatible_http('https://example.invalid',{},
            {'model':'free-test'},'OpenRouter','synthetic',max_provider_attempts=1)
    assert cancelled==[True]


@pytest.mark.asyncio
async def test_openrouter_completion_budget_is_forwarded(monkeypatch):
    wrapper=LLMClientWrapper()
    sent=[]
    async def execute(url,headers,payload,*args):
        sent.append(payload)
        return {}
    monkeypatch.setattr(wrapper,'_execute_openai_compatible_http',execute)
    await wrapper._call_openrouter('system','synthetic','candidate',1)
    assert sent[0]['max_tokens']==settings.OPENROUTER_MAX_TOKENS
    assert sent[0]['response_format']['type']=='json_schema'


@pytest.mark.asyncio
async def test_gemini_usage_includes_thinking_without_logging_input(caplog):
    async def generate(**kwargs):
        return SimpleNamespace(text='{}',model_version='gemini-test',usage_metadata=SimpleNamespace(
            prompt_token_count=100,candidates_token_count=120,thoughts_token_count=80,total_token_count=300))
    wrapper=LLMClientWrapper()
    wrapper.gemini_client=SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate)))
    with caplog.at_level(logging.INFO,logger='cv_screening'):
        assert await wrapper._call_gemini('private system','private candidate','synthetic',1)=={}
    events=[record for record in caplog.records if getattr(record,'event',None)=='provider_response']
    assert len(events)==1
    assert (events[0].input_tokens,events[0].output_tokens,events[0].total_tokens)==(100,200,300)
    assert 'private' not in caplog.text


@pytest.mark.asyncio
async def test_gemini_rate_limit_leaves_retry_to_campaign():
    calls=[]
    class Limited(Exception):
        code=429
        headers={'retry-after':'120'}
    async def generate(**kwargs):
        calls.append(kwargs)
        raise Limited()
    wrapper=LLMClientWrapper()
    wrapper.gemini_client=SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate)))
    with pytest.raises(ProviderRateLimited) as result:
        await wrapper._call_gemini('system','synthetic','candidate',1)
    assert result.value.retry_after==120 and len(calls)==1


@pytest.mark.asyncio
async def test_gemini_server_failure_is_retryable_without_an_internal_second_call():
    calls=[]
    class Unavailable(Exception):
        code=503
    async def generate(**kwargs):
        calls.append(kwargs)
        raise Unavailable()
    wrapper=LLMClientWrapper()
    wrapper.gemini_client=SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate)))
    with pytest.raises(ProviderTransientFailure):
        await wrapper._call_gemini('system','synthetic','candidate',1)
    assert len(calls)==1
