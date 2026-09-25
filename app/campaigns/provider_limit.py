"""Shared Redis admission for Stage 3 requests across campaigns and workers."""
import random
from uuid import uuid4

from redis.asyncio import Redis

from app.config import settings


_ADMIT = """
local clock = redis.call('TIME')
local now = tonumber(clock[1]) + tonumber(clock[2]) / 1000000
local requests = KEYS[1]
local tokens = KEYS[2]
local active = KEYS[3]
local old = redis.call('ZRANGEBYSCORE', requests, '-inf', now - 60)
for _, id in ipairs(old) do redis.call('HDEL', tokens, id) end
redis.call('ZREMRANGEBYSCORE', requests, '-inf', now - 60)
redis.call('ZREMRANGEBYSCORE', active, '-inf', now)
local used = 0
for _, value in ipairs(redis.call('HVALS', tokens)) do used = used + tonumber(value) end
if redis.call('ZCARD', active) >= tonumber(ARGV[4]) then return 2 end
if redis.call('ZCARD', requests) >= tonumber(ARGV[2]) or used + tonumber(ARGV[5]) > tonumber(ARGV[3]) then
    local first = redis.call('ZRANGE', requests, 0, 0, 'WITHSCORES')
    if #first == 0 then return 60 end
    return math.max(1, math.ceil(60 + tonumber(first[2]) - now))
end
redis.call('ZADD', requests, now, ARGV[1])
redis.call('HSET', tokens, ARGV[1], ARGV[5])
redis.call('ZADD', active, now + tonumber(ARGV[6]), ARGV[1])
redis.call('EXPIRE', requests, 120)
redis.call('EXPIRE', tokens, 120)
redis.call('EXPIRE', active, tonumber(ARGV[6]) + 60)
return 0
"""


def retry_delay(attempt: int, retry_after: float | None = None) -> int:
    base = min(settings.CAMPAIGN_STAGE3_RETRY_MAX_SECONDS,
               settings.CAMPAIGN_STAGE3_RETRY_BASE_SECONDS * 2 ** max(0, attempt - 1))
    jittered = base + random.uniform(0, base / 4)
    # The configured cap bounds our backoff; a valid provider cooldown takes precedence.
    return max(1, int(max(jittered, min(86400, retry_after or 0))))


async def admit(redis: Redis) -> tuple[str | None, int]:
    provider = settings.LLM_PROVIDER.lower()
    prefix = f"campaign:provider:{provider}"
    token = uuid4().hex
    wait = int(await redis.eval(_ADMIT, 3, prefix + ":requests", prefix + ":tokens",
        prefix + ":active", token, settings.PROVIDER_REQUESTS_PER_MINUTE,
        settings.PROVIDER_TOKENS_PER_MINUTE, settings.CAMPAIGN_STAGE3_GLOBAL_INFLIGHT,
        settings.PROVIDER_TOKENS_PER_REQUEST, settings.PROVIDER_TIMEOUT_SECONDS + 30))
    return (token if wait == 0 else None, wait)


async def release(redis: Redis, token: str):
    await redis.zrem(f"campaign:provider:{settings.LLM_PROVIDER.lower()}:active", token)
