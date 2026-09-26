"""Identify Stage 0 work already queued or reserved by the Redis broker."""
import base64
import json


def stage0_cv_id(message):
    try:
        if message.get("headers", {}).get("task") != "campaign.stage0":
            return None
        args = json.loads(base64.b64decode(message["body"]))[0]
        return args[0] if isinstance(args, list) and args and isinstance(args[0], str) else None
    except (KeyError, ValueError, TypeError):
        return None


async def queued_stage0_ids(redis):
    present = set()
    # Kombu's default Redis priority steps; include reserved late-ack deliveries.
    for suffix in ("", "\x06\x163", "\x06\x166", "\x06\x169"):
        offset = 0
        while True:
            batch = await redis.lrange("ocr" + suffix, offset, offset + 499)
            for raw in batch:
                try:
                    cv_id = stage0_cv_id(json.loads(raw))
                    if cv_id:
                        present.add(cv_id)
                except (ValueError, TypeError):
                    continue
            if len(batch) < 500:
                break
            offset += 500
    async for _, raw in redis.hscan_iter("unacked", count=500):
        try:
            cv_id = stage0_cv_id(json.loads(raw)[0])
            if cv_id:
                present.add(cv_id)
        except (ValueError, TypeError, IndexError):
            continue
    return present
