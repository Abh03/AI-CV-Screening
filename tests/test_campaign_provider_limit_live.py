"""Run with --run-infrastructure against a disposable Redis test instance."""
import asyncio
import os

import pytest
from redis.asyncio import Redis

from app.campaigns.provider_limit import admit, release
from app.config import settings


@pytest.mark.infrastructure
@pytest.mark.asyncio
async def test_global_provider_admission_across_independent_clients(monkeypatch):
    url = os.getenv("PHASE5_REDIS_TEST_URL", "redis://127.0.0.1:56379/0")
    clients = [Redis.from_url(url) for _ in range(12)]
    try:
        await clients[0].flushdb()
        monkeypatch.setattr(settings, "CAMPAIGN_STAGE3_GLOBAL_INFLIGHT", 2)
        monkeypatch.setattr(settings, "PROVIDER_REQUESTS_PER_MINUTE", 3)
        monkeypatch.setattr(settings, "PROVIDER_TOKENS_PER_MINUTE", 12000)
        monkeypatch.setattr(settings, "PROVIDER_TOKENS_PER_REQUEST", 6000)
        burst = await asyncio.gather(*(admit(client) for client in clients))
        accepted = [(index, token) for index, (token, _) in enumerate(burst) if token]
        assert len(accepted) == 2
        assert all(wait > 0 for token, wait in burst if token is None)
        for index, token in accepted:
            await release(clients[index], token)
        # Released concurrency still cannot exceed the shared token minute budget.
        assert (await admit(clients[0]))[0] is None
        await clients[0].flushdb()
        monkeypatch.setattr(settings, "PROVIDER_TOKENS_PER_MINUTE", 18000)
        first = await asyncio.gather(*(admit(client) for client in clients[:2]))
        assert len([token for token, _ in first if token]) == 2
        await release(clients[0], first[0][0])
        third, _ = await admit(clients[2])
        assert third is not None
        await release(clients[1], first[1][0])
        await release(clients[2], third)
        assert (await admit(clients[3]))[0] is None
    finally:
        await clients[0].flushdb()
        await asyncio.gather(*(client.aclose() for client in clients))
