import pytest
from app.campaigns.wakeup import schedule_wakeup


class Redis:
    def __init__(self):
        self.keys={}

    async def set(self,key,value,*,nx,ex):
        assert nx and ex>0
        if key in self.keys:
            return False
        self.keys[key]=(value,ex)
        return True

    async def delete(self,key):
        self.keys.pop(key,None)


@pytest.mark.asyncio
async def test_waiting_pair_burst_schedules_one_timer_per_campaign():
    redis=Redis()
    published=[]
    for _ in range(120):
        await schedule_wakeup(redis,'campaign-one',60,lambda **kwargs:published.append(kwargs))
    await schedule_wakeup(redis,'campaign-two',30,lambda **kwargs:published.append(kwargs))
    assert published==[{'args':['campaign-one'],'countdown':60},{'args':['campaign-two'],'countdown':30}]


@pytest.mark.asyncio
async def test_failed_publication_does_not_block_next_wakeup():
    redis=Redis()
    def fail(**kwargs):
        raise RuntimeError('broker unavailable')
    assert not await schedule_wakeup(redis,'campaign',60,fail)
    published=[]
    assert await schedule_wakeup(redis,'campaign',60,lambda **kwargs:published.append(kwargs))
    assert len(published)==1
