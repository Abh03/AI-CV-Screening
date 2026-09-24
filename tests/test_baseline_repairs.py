import asyncio
import json
import os
import subprocess
import sys

import pytest

from app.stage3_evaluation.llm_client import (
    LLMClientWrapper,
    evaluate_candidate_batch_async,
    evaluate_single_candidate_async,
    llm_client,
)


def test_imports_do_not_connect_or_require_provider_credentials():
    script = """
import psycopg2
import asyncpg
def forbidden(*args, **kwargs):
    raise AssertionError('Connection during import')
psycopg2.connect = forbidden
asyncpg.connect = forbidden
import app.main
import app.core.database
from app.config import settings
from app.models.database import engine
from app.stage3_evaluation.llm_client import llm_client
assert engine.url == __import__('sqlalchemy').engine.make_url(settings.DATABASE_URL)
assert not llm_client._initialized
"""
    env = dict(os.environ, LLM_PROVIDER="gemini", GEMINI_API_KEY="")
    result = subprocess.run([sys.executable, "-c", script], env=env, capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stderr


@pytest.mark.asyncio
async def test_default_evaluation_uses_configured_client(monkeypatch):
    calls = []

    async def generate(*, system_prompt, user_prompt, candidate_id):
        calls.append(candidate_id)
        return LLMClientWrapper._call_mock(candidate_id)

    monkeypatch.setattr(llm_client, "generate_evaluation", generate)
    result = await evaluate_single_candidate_async({"candidate_id": "configured"}, {})
    assert calls == ["configured"]
    assert result.composite_score > 0


@pytest.mark.asyncio
async def test_restored_retry_and_concurrency_contract():
    class Provider:
        active = 0
        peak = 0
        calls = 0

        async def generate_structured_evaluation(self, **kwargs):
            self.calls += 1
            self.active += 1
            self.peak = max(self.peak, self.active)
            try:
                await asyncio.sleep(0.01)
                if self.calls == 1:
                    return "invalid JSON"
                return json.dumps(LLMClientWrapper._call_mock("test"))
            finally:
                self.active -= 1

    retry_provider = Provider()
    result = await evaluate_single_candidate_async({}, {}, retry_provider, max_retries=1)
    assert retry_provider.calls == 2
    assert result.composite_score > 0
    provider = Provider()
    results = await evaluate_candidate_batch_async(
        [{"candidate_id": str(i)} for i in range(8)], {}, provider, concurrency_limit=2
    )
    assert len(results) == 8
    assert provider.peak == 2
    with pytest.raises(ValueError):
        await evaluate_candidate_batch_async([], {}, concurrency_limit=0)


def test_sync_diagnostics_convert_url_without_changing_settings(monkeypatch):
    from app.core import database
    from unittest.mock import MagicMock

    connect = MagicMock()
    monkeypatch.setattr(database.psycopg2, "connect", connect)
    monkeypatch.setattr(database.settings, "DATABASE_URL", "postgresql+asyncpg://user:pass@localhost/test")
    with database.get_db_connection():
        pass
    connect.assert_called_once_with("postgresql://user:pass@localhost/test")
    connect.return_value.commit.assert_called_once()
    connect.return_value.close.assert_called_once()
