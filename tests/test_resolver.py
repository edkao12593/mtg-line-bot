import asyncio
from mtg_bot.models import CardQuery
from mtg_bot.resolver import Resolver
from mtg_bot.scryfall import ScryfallError


async def test_singleflight_cache_order_and_partial_failure(card):
    calls = []
    class Client:
        async def get_card_by_name(self, name):
            calls.append(name)
            await asyncio.sleep(.01)
            if name == 'bad': raise ScryfallError('not_found')
            return card
    r = Resolver(Client())
    a, bad = CardQuery('Sol Ring','sol ring'), CardQuery('bad','bad')
    results = await asyncio.gather(r.resolve_many((a,bad)),r.resolve(a))
    assert calls.count('Sol Ring') == 1
    assert results[0][0].card == card
    assert results[0][1].error == 'not_found'
    await r.resolve_many((a,bad))
    assert len(calls) == 2


async def test_transient_error_not_cached():
    count = [0]
    class Client:
        async def get_card_by_name(self,name):
            count[0] += 1
            raise ScryfallError('unavailable')
    r = Resolver(Client())
    for _ in range(2): await r.resolve(CardQuery('a','a'))
    assert count[0] == 2


async def test_cancelled_waiter_does_not_cancel_shared_lookup(card):
    release = asyncio.Event()
    class Client:
        async def get_card_by_name(self,name):
            await release.wait()
            return card
    r = Resolver(Client())
    q = CardQuery('a','a')
    first = asyncio.create_task(r.resolve(q))
    second = asyncio.create_task(r.resolve(q))
    await asyncio.sleep(0)
    first.cancel()
    await asyncio.gather(first,return_exceptions=True)
    release.set()
    assert (await second).card == card
