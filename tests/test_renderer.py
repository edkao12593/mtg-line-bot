from dataclasses import replace

import pytest

from mtg_bot.models import (
    Card,
    CardFace,
    CardQuery,
    CardResponse,
    CardResult,
    TextResponse,
)
from mtg_bot.renderer.cards import CardRenderer, card_panels


@pytest.mark.parametrize("layout", ["split", "adventure", "flip"])
def test_whole_image_precedes_faces(layout):
    card = Card(
        "a",
        "a",
        layout,
        "uri",
        "https://cards.scryfall.io/a",
        (CardFace("face", "https://cards.scryfall.io/f"),),
    )
    assert card_panels(card) == [("a", "https://cards.scryfall.io/a")]


@pytest.mark.parametrize(
    "layout", ["transform", "modal_dfc", "double_faced_token", "reversible_card"]
)
def test_both_faces(layout):
    card = Card(
        "a",
        "a",
        layout,
        "uri",
        None,
        (
            CardFace("front", "https://cards.scryfall.io/f"),
            CardFace("back", "https://cards.scryfall.io/b"),
        ),
    )
    assert [name for name, _ in card_panels(card)] == ["front", "back"]


async def test_renderer_preserves_successes_errors_and_notices(card):
    results = [
        CardResult(CardQuery("Sol Ring", "sol ring"), card),
        CardResult(CardQuery("missing", "missing"), error="not_found"),
    ]
    responses = await CardRenderer().render(results, ("已達查詢數量上限。",))
    assert isinstance(responses[0], CardResponse)
    assert responses[0].card == card
    assert responses[1] == TextResponse("missing：查不到卡。")
    assert responses[2] == TextResponse("已達查詢數量上限。")


async def test_renderer_reports_missing_print():
    query = CardQuery("Jace", "key", set_code="wwk", collector_number="999")
    responses = await CardRenderer().render([CardResult(query, error="not_found")])
    assert responses == [TextResponse("Jace|wwk|999：指定系列或版本中查不到卡。")]


async def test_renderer_cleans_links_without_changing_card(card):
    original = replace(card, scryfall_uri=card.scryfall_uri + "?utm_source=api#details")
    response = (
        await CardRenderer().render(
            [CardResult(CardQuery("Sol Ring", "sol ring"), original)]
        )
    )[0]
    assert response.card.scryfall_uri == card.scryfall_uri
    assert original.scryfall_uri.endswith("?utm_source=api#details")
