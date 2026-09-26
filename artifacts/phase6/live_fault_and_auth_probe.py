"""Isolated live PostgreSQL/Redis checks; injected evaluator makes no provider calls."""
import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from unittest.mock import patch

import httpx
from sqlalchemy import delete, select
from app.core.auth import hash_password, issue_access_token
from app.models.database import (AsyncSessionLocal, CampaignModel, CampaignJDModel,
                                 CampaignPairModel, RecruiterUserModel)
from app.campaigns.persistence import reserve_campaign, reserve_cv, finish_stage0
from app.stage3_evaluation.llm_client import ProviderRateLimited, ProviderTransientFailure
from app.stage3_evaluation.schemas import LLMEvaluationOutput
from app.stage3_evaluation.evaluator import compute_deterministic_tier
from app.workers import tasks


async def main():
    transient = len(sys.argv)>1 and sys.argv[1]=='503'
    identifier = "capacity-probe-" + uuid4().hex[:16]
    campaign_id = None
    events = []
    original_provider = tasks.settings.LLM_PROVIDER
    original_budget = tasks.settings.PROVIDER_TOKENS_PER_MINUTE
    tasks.settings.LLM_PROVIDER = identifier
    tasks.settings.PROVIDER_TOKENS_PER_MINUTE = 12000
    try:
        async with AsyncSessionLocal() as db:
            user = RecruiterUserModel(id=identifier, username=identifier,
                email=identifier + "@example.invalid", role="recruiter", is_active=True,
                password_hash=hash_password(uuid4().hex), token_version=0,
                failed_attempts=0)
            db.add(user)
            await db.commit()
            token, _ = issue_access_token(user)
        async with httpx.AsyncClient(base_url="http://web:8000", timeout=30) as client:
            for headers in ({}, {"Authorization": "Bearer invalid-capacity-probe"}):
                response = await client.get('/api/v1/campaigns/0dcf5e5a-2dcb-4042-9e1f-51288a1687ba', headers=headers)
                events.append({"step": "unauthorized", "input": "missing" if not headers else "invalid token",
                               "http_status": response.status_code, "output": response.json()})
                assert response.status_code == 401
            headers = {"Authorization": "Bearer " + token}
            for path in ("/api/v1/auth/me", "/api/v1/campaigns/0dcf5e5a-2dcb-4042-9e1f-51288a1687ba",
                         "/api/v1/jds/approved"):
                response = await client.get(path, headers=headers)
                events.append({"step": "cross_owner", "input": path, "http_status": response.status_code,
                               "output": response.json()})
        async with AsyncSessionLocal() as db:
            _, campaign = await reserve_campaign(db, owner_id=identifier, request_hash=uuid4().hex,
                job_snapshots=[{"job_id": "fault-probe", "title": "Isolated fault probe"}],
                policy_snapshots=[{"stage3_cap": 30}])
            campaign_id = campaign.id
            await reserve_cv(db, campaign_id=campaign_id, owner_id=identifier, candidate_id=identifier,
                             source_filename="probe.pdf", content_hash=uuid4().hex)
            await finish_stage0(db, campaign_id=campaign_id, owner_id=identifier, candidate_id=identifier,
                               redacted_text="Synthetic evidence", source_locations=[])
            pair = (await db.execute(select(CampaignPairModel).where(
                CampaignPairModel.campaign_id == campaign_id))).scalar_one()
            evidence = {"candidate_id": identifier, "evidence_by_category": {
                category: [{"candidate_id": identifier, "text": category + " synthetic evidence",
                            "source_location": {"page_number": 1}}]
                for category in ("SKILLS", "EXPERIENCE", "PROJECTS", "EDUCATION")}}
            pair.status = "STAGE3_RUNNING"
            pair.stage2_rank = 1
            pair.lease_owner = "probe-first"
            pair.lease_until = datetime.now(timezone.utc) + timedelta(minutes=10)
            pair.result_snapshot = {"stage2_evidence": evidence}
            pair_id = pair.id
            await db.commit()
        calls = []
        async def evaluate(payload, job, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                raise (ProviderTransientFailure if transient else ProviderRateLimited)(120)
            output = LLMEvaluationOutput.model_validate({
                **{category.lower(): {"score": 85, "rationale": "Synthetic evidence for an isolated recovery check",
                                     "citations": [category + ":1"]}
                   for category in ("SKILLS", "EXPERIENCE", "PROJECTS", "EDUCATION")},
                "flags": [], "executive_summary": "Isolated retry test"})
            return compute_deterministic_tier(identifier, output, payload)
        with patch.object(tasks, "evaluate_single_candidate_async", evaluate), \
             patch.object(tasks.campaign_coordinate_task, "apply_async"), \
             patch.object(tasks.campaign_coordinate_task, "delay"):
            first = await tasks.execute_campaign_stage3(pair_id, "probe-first")
            duplicate = await tasks.execute_campaign_stage3(pair_id, "probe-first")
            async with AsyncSessionLocal() as db:
                pair = await db.get(CampaignPairModel, pair_id)
                wait = (pair.lease_until - datetime.now(timezone.utc)).total_seconds()
                events.append({"step": "injected_503" if transient else "injected_429", "input": {"retry_after_seconds": 120},
                               "output": {"first": first, "duplicate": duplicate,
                                          "status": pair.status, "attempts": pair.stage3_attempt_count,
                                          "remaining_wait_seconds": round(wait, 3)}})
                assert first == ('TRANSIENT_RETRY' if transient else 'RATE_LIMITED') and duplicate == "LEASE_LOST" and wait > 100
                pair.status = "STAGE3_RUNNING"
                pair.lease_owner = "probe-retry"
                pair.lease_until = datetime.now(timezone.utc) + timedelta(minutes=10)
                await db.commit()
            retry = await tasks.execute_campaign_stage3(pair_id, "probe-retry")
            duplicate = await tasks.execute_campaign_stage3(pair_id, "probe-retry")
            async with AsyncSessionLocal() as db:
                pairs = (await db.execute(select(CampaignPairModel).where(
                    CampaignPairModel.campaign_id == campaign_id))).scalars().all()
                events.append({"step": "retry_and_duplicate", "output": {"retry": retry,
                               "duplicate": duplicate, "stored_pairs": len(pairs),
                               "attempts": pairs[0].stage3_attempt_count, "injected_calls": len(calls)}})
                assert retry == "SUCCESS" and duplicate == "LEASE_LOST" and len(pairs) == 1
                assert pairs[0].stage3_attempt_count == 2 and len(calls) == 2
        from app.campaigns.wakeup import schedule_wakeup
        from redis.asyncio import Redis
        redis = Redis.from_url(tasks.settings.REDIS_URL)
        published = []
        wakeup_id = identifier + '-wakeup'
        for _ in range(120):
            await schedule_wakeup(redis, wakeup_id, 60, lambda **kwargs: published.append(kwargs))
        ttl = await redis.ttl('campaign:wakeup:' + wakeup_id)
        assert len(published) == 1 and 0 < ttl <= 60
        events.append({'step':'coalesced_wakeups','input':{'waiting_pair_notifications':120},
                       'output':{'scheduled_timers':len(published),'redis_ttl_seconds':ttl}})
        await redis.delete('campaign:wakeup:' + wakeup_id)
        await redis.aclose()
    finally:
        from redis.asyncio import Redis
        redis = Redis.from_url(tasks.settings.REDIS_URL)
        await redis.delete(*(f'campaign:provider:{identifier}:{suffix}' for suffix in ('requests', 'tokens', 'active')))
        if campaign_id:
            await redis.delete('campaign:wakeup:' + campaign_id)
        await redis.aclose()
        tasks.settings.LLM_PROVIDER = original_provider
        tasks.settings.PROVIDER_TOKENS_PER_MINUTE = original_budget
        async with AsyncSessionLocal() as db:
            if campaign_id:
                await db.execute(delete(CampaignModel).where(CampaignModel.id == campaign_id))
            await db.execute(delete(RecruiterUserModel).where(RecruiterUserModel.id == identifier))
            await db.commit()
        await tasks.engine.dispose()
    print(json.dumps({"provider_calls": 0, "events": events, "isolated_records_removed": True}, indent=2))


asyncio.run(main())
