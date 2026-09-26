import base64
import json

import pytest

from app.campaigns.broker_recovery import queued_stage0_ids, stage0_cv_id


def message(cv_id, task="campaign.stage0"):
    return {"headers": {"task": task}, "body": base64.b64encode(
        json.dumps([[cv_id], {}, {}]).encode()).decode()}


@pytest.mark.asyncio
async def test_recovery_excludes_queued_priority_and_reserved_documents():
    class Broker:
        async def lrange(self, key, start, end):
            return {"ocr": [json.dumps(message("queued")), "malformed"],
                    "ocr\x06\x163": [json.dumps(message("priority"))]}.get(key, [])

        async def hscan_iter(self, key, count):
            yield "delivery", json.dumps([message("reserved"), "", "ocr"])
            yield "other", json.dumps([message("unrelated", "campaign.stage2_pair"), "", "retrieval"])

    assert await queued_stage0_ids(Broker()) == {"queued", "priority", "reserved"}


def test_recovery_ignores_invalid_messages():
    assert stage0_cv_id({"headers": {"task": "campaign.stage0"}, "body": "invalid"}) is None
    assert stage0_cv_id({}) is None
