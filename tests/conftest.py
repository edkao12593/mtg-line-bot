import pytest

from mtg_bot.models import Card


@pytest.fixture
def card():
    return Card(
        "id",
        "Sol Ring",
        "normal",
        "https://scryfall.com/card/test",
        "https://cards.scryfall.io/normal/front/a/b/test.jpg",
    )
