import asyncio
import json
from dataclasses import replace

import httpx
import pytest

from mtg_bot.line_adapter import to_line_messages
from mtg_bot.models import CardResponse, Detail, TextResponse
from mtg_bot.parser import parse_queries
from mtg_bot.renderer.cards import CardRenderer
from mtg_bot.resolver import Resolver
from mtg_bot.scryfall import ScryfallClient, ScryfallError


@pytest.mark.parametrize(
    "raw,mode",
    [
        ("!$", "image"),
        ("$!", "prices"),
        ("!?", "image"),
        ("?#", "rulings"),
        ("#", "legality"),
    ],
)
def test_first_prefix(raw, mode):
    assert parse_queries(f"[[{raw}Sol Ring]]").queries[0].mode == mode


def test_empty_prefix_and_version_validation():
    assert not parse_queries("[[$]] [[?]] [[#]] [[!]]").queries
    parsed = parse_queries(
        "[[Jace|WWK|31a]] [[Sol Ring|wwk|31a]] [[Sol Ring|CMM]] [[Sol Ring]]"
    )
    assert len(parsed.queries) == 4
    assert parsed.queries[0].collector_number == "31a"
    assert parse_queries("[[A||31]] [[A|B|1|extra]]").notices
    assert parse_queries("[[Jace|WWK|]]").queries[0].collector_number is None


def payload(name="Sol Ring", **extra):
    return {
        "id": "a",
        "name": name,
        "layout": "normal",
        "scryfall_uri": "https://scryfall.com/card/a?utm_source=api",
        "prints_search_uri": "https://api.scryfall.com/cards/search?q=oracleid%3Aa&unique=prints",
        **extra,
    }


async def run(text, mock):
    async with httpx.AsyncClient(
        trust_env=False, transport=httpx.MockTransport(mock)
    ) as http:
        resolver = Resolver(ScryfallClient(http, user_agent="test"))
        try:
            results = await resolver.resolve_many(parse_queries(text).queries)
            return results, await CardRenderer().render(results)
        finally:
            await resolver.close()


async def test_nonexistent_and_ambiguous_do_not_break_batch():
    def mock(req):
        name = req.url.params.get("fuzzy")
        if name == "bad":
            return httpx.Response(404, json={"type": "not_found"})
        if name == "thalia":
            return httpx.Response(404, json={"type": "ambiguous"})
        return httpx.Response(200, json=payload())

    results, responses = await run("[[bad]] [[thalia]] [[Sol Ring]]", mock)
    assert [r.error for r in results] == ["not_found", "ambiguous", None]
    assert "查不到卡" in responses[0].text and "更完整" in responses[1].text
    assert isinstance(responses[2], CardResponse)


async def test_set_and_collector_routing_and_errors():
    calls = []

    def mock(req):
        calls.append(req)
        if req.url.path.endswith("/999"):
            return httpx.Response(404, json={})
        if req.url.params.get("set") == "zzzz":
            return httpx.Response(404, json={})
        return httpx.Response(200, json=payload("Jace"))

    results, responses = await run(
        "[[wrongname|WWK|31]] [[Jace|wwk]] [[Jace|WWK|999]] [[Sol Ring|ZZZZ]]", mock
    )
    assert any(r.url.path == "/cards/wwk/31" for r in calls)
    assert any(r.url.params.get("set") == "wwk" for r in calls)
    assert results[0].card.name == "Jace"
    assert all("指定系列或版本" in r.text for r in responses[2:])


async def test_price_query_preserves_search_and_cache_and_modes():
    calls = []

    def mock(req):
        calls.append(req)
        if req.url.path == "/cards/search":
            assert req.url.params["q"] == "oracleid:a"
            assert req.url.params["unique"] == "prints"
            assert req.url.params["order"] == "released"
            return httpx.Response(
                200,
                json={
                    "data": [
                        payload(
                            set_name="Set A",
                            collector_number="1",
                            prices={
                                "usd": None,
                                "usd_foil": "1.23",
                                "eur_etched": "2.34",
                            },
                        ),
                        payload(
                            set_name="Set B", collector_number="2", prices={"usd": None}
                        ),
                    ],
                    "has_more": True,
                },
            )
        return httpx.Response(200, json=payload())

    async with httpx.AsyncClient(
        trust_env=False, transport=httpx.MockTransport(mock)
    ) as http:
        resolver = Resolver(ScryfallClient(http, user_agent="test"))
        try:
            queries = parse_queries("[[Sol Ring]] [[$Sol Ring]] [[$sol ring]]").queries
            first, second = await asyncio.gather(
                resolver.resolve_many(queries), resolver.resolve_many(queries)
            )
            assert len(calls) == 2 and len(first) == 2
            assert first[1].more
            assert "USD foil 1.23" in first[1].details[0].body
            assert len(first[1].details) == 1
            await resolver.resolve_many(queries)
            assert len(calls) == 2
        finally:
            await resolver.close()


@pytest.mark.parametrize(
    "rows,expected_more",
    [
        ([], False),
        ([{"published_at": "2020-01-01", "comment": "x" * 4000}], True),
        (
            [
                {"published_at": "2020-01-01", "comment": f"ruling {i}"}
                for i in range(12)
            ],
            True,
        ),
    ],
)
async def test_rulings_empty_long_many(rows, expected_more):
    def mock(req):
        return httpx.Response(
            200, json={"data": rows} if req.url.path.endswith("/rulings") else payload()
        )

    results, responses = await run("[[?Sol Ring]]", mock)
    result = results[0]
    assert result.more == expected_more and len(result.details) <= 8
    serialized = json.dumps(
        to_line_messages(responses)[0].to_dict(), ensure_ascii=False
    )
    assert "#rulings" in serialized and "#%23rulings" not in serialized
    assert "utm_source" not in serialized
    if not rows:
        assert "沒有裁定" in serialized
    if expected_more:
        assert "更多裁定" in serialized
    if rows and len(rows[0]["comment"]) > 2400:
        assert "已截斷" in serialized


async def test_rulings_failure_isolated_and_not_cached():
    calls = []

    def mock(req):
        calls.append(req)
        if req.url.path.endswith("/rulings"):
            return httpx.Response(400, json={})
        return httpx.Response(200, json=payload())

    results, responses = await run("[[?Sol Ring]] [[Sol Ring]]", mock)
    assert results[0].error == "unavailable" and results[1].card
    assert isinstance(responses[0], TextResponse)
    assert "查詢暫時失敗" in responses[0].text


def test_legalities_unknown_and_missing(card):
    card = replace(
        card,
        legalities=(
            ("modern", "banned"),
            ("vintage", "restricted"),
            ("newformat", "newstatus"),
        ),
    )
    data = json.dumps(to_line_messages([CardResponse(card, "legality")])[0].to_dict())
    assert "Banned" in data and "Restricted" in data and "newformat: newstatus" in data


async def test_missing_price_data():
    def mock(req):
        return httpx.Response(
            200,
            json={"data": [payload(prices={"usd": None})]}
            if req.url.path == "/cards/search"
            else payload(),
        )

    _, responses = await run("[[$Sol Ring]]", mock)
    assert "暫無可用價格" in json.dumps(
        to_line_messages(responses)[0].to_dict(), ensure_ascii=False
    )


def test_nine_long_ruling_bubbles_fit_and_label_truncation(card):
    response = CardResponse(
        card,
        "rulings",
        tuple(Detail("2020-01-01", "裁定" * 1000) for _ in range(8)),
        True,
    )
    data = to_line_messages([response] * 9)[0].to_dict()
    assert len(json.dumps(data["contents"], ensure_ascii=False).encode()) <= 49000
    assert "已截斷" in json.dumps(data, ensure_ascii=False)
    assert "#rulings" in json.dumps(data)


async def test_untrusted_prints_uri_rejected():
    async with httpx.AsyncClient(
        trust_env=False,
        transport=httpx.MockTransport(lambda r: pytest.fail("unexpected request")),
    ) as http:
        client = ScryfallClient(http, user_agent="test")
        with pytest.raises(ScryfallError):
            await client._request("https://example.com/private")


async def test_detail_transient_failure_is_not_cached():
    attempts = 0

    def mock(req):
        nonlocal attempts
        if req.url.path.endswith("/rulings"):
            attempts += 1
            return (
                httpx.Response(400, json={})
                if attempts == 1
                else httpx.Response(200, json={"data": []})
            )
        return httpx.Response(200, json=payload())

    async with httpx.AsyncClient(
        trust_env=False, transport=httpx.MockTransport(mock)
    ) as http:
        resolver = Resolver(ScryfallClient(http, user_agent="test"))
        try:
            q = parse_queries("[[?Sol Ring]]").queries[0]
            assert (await resolver.resolve(q)).error == "unavailable"
            assert (await resolver.resolve(q)).error is None
            assert attempts == 2
        finally:
            await resolver.close()


async def test_every_endpoint_passes_shared_limiter():
    class Gate:
        calls = 0

        async def acquire(self):
            self.calls += 1

    gate = Gate()

    def mock(req):
        if req.url.path.endswith("/rulings"):
            return httpx.Response(200, json={"data": []})
        if req.url.path == "/cards/search":
            return httpx.Response(200, json={"data": []})
        return httpx.Response(200, json=payload())

    async with httpx.AsyncClient(
        trust_env=False, transport=httpx.MockTransport(mock)
    ) as http:
        client = ScryfallClient(http, user_agent="test", limiter=gate)
        card = await client.get_card_by_name("Sol Ring", "cmm")
        await client.get_card_by_collector("wwk", "31a")
        await client.get_rulings(card)
        await client.get_price_prints(card)
        assert gate.calls == 4
