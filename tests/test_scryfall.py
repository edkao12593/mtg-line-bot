import asyncio
import httpx
import pytest
from mtg_bot.scryfall import ScryfallClient, ScryfallError, RateLimiter, safe_image_url


def payload():
    return {'id':'a', 'name':'Sol Ring', 'layout':'normal', 'scryfall_uri':'https://scryfall.com/card/a', 'image_uris': {'normal':'https://cards.scryfall.io/normal/a.jpg'}}


async def test_rate_gate_concurrent_and_no_burst():
    now, starts = [100.0], []
    async def sleep(delay):
        now[0] += delay
    gate = RateLimiter(clock=lambda: now[0], sleep=sleep)
    async def call():
        await gate.acquire()
        starts.append(now[0])
    await asyncio.gather(*(call() for _ in range(12)))
    assert all(b-a >= .119999 for a,b in zip(starts, starts[1:]))
    await gate.cooldown(3)
    await call()
    assert starts[-1] - starts[-2] >= 3


async def test_headers_fuzzy_retry_and_gate():
    calls = []
    class Gate:
        async def acquire(self): calls.append('gate')
    def mock(request):
        assert request.url.params['fuzzy'] == 'Sol Ring'
        assert request.headers['user-agent'] == 'bot/contact'
        assert request.headers['accept'] == 'application/json'
        calls.append('http')
        return httpx.Response(503 if len(calls) == 2 else 200, json=payload())
    async with httpx.AsyncClient(transport=httpx.MockTransport(mock)) as http:
        client = ScryfallClient(http, user_agent='bot/contact', limiter=Gate())
        assert (await client.get_card_by_name('Sol Ring')).name == 'Sol Ring'
    assert calls == ['gate', 'http', 'gate', 'http']


@pytest.mark.parametrize('status,data,kind', [(404,{},'not_found'),(404,{'type':'ambiguous'},'ambiguous'),(400,{},'unavailable'),(200,{},'unavailable')])
async def test_typed_errors(status,data,kind):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(status,json=data))) as http:
        with pytest.raises(ScryfallError) as error:
            await ScryfallClient(http,user_agent='test').get_card_by_name('a')
    assert error.value.kind == kind


async def test_429_no_retry_global_cooldown():
    count = [0]
    gate = RateLimiter()
    def mock(_):
        count[0] += 1
        return httpx.Response(429,headers={'Retry-After':'4'})
    async with httpx.AsyncClient(transport=httpx.MockTransport(mock)) as http:
        with pytest.raises(ScryfallError):
            await ScryfallClient(http,user_agent='test',limiter=gate).get_card_by_name('a')
    assert count[0] == 1
    assert gate.next_at > gate.clock() + 3.5


@pytest.mark.parametrize('url', ['http://cards.scryfall.io/a', 'https://evil.com/a', 'https://scryfall.io.evil.com/a', 'https://user:pw@cards.scryfall.io/a'])
def test_image_url_allowlist(url):
    assert safe_image_url(url) is None
