"""Coalesce delayed coordinator notifications; durable rows and beat remain authoritative."""


async def schedule_wakeup(redis, campaign_id, delay, publish):
    key = f'campaign:wakeup:{campaign_id}'
    delay = max(1, int(delay))
    try:
        if not await redis.set(key, 'scheduled', nx=True, ex=delay):
            return False
        publish(args=[campaign_id], countdown=delay)
        return True
    except Exception:
        # A failed publication must not suppress a subsequent notification.
        # Beat also scans the durable cooldowns every 30 seconds.
        try:
            await redis.delete(key)
        except Exception:
            pass
        return False
