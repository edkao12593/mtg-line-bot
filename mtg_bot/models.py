from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class CardQuery:
    name: str
    key: str
    mode: Literal["text", "image", "prices", "rulings", "legality"] = "text"
    set_code: str | None = None
    collector_number: str | None = None


@dataclass(frozen=True)
class ParseResult:
    queries: tuple[CardQuery, ...]
    notices: tuple[str, ...] = ()


@dataclass(frozen=True)
class CardFace:
    name: str
    image_url: str | None
    mana_cost: str = ""
    type_line: str = ""
    oracle_text: str = ""
    flavor_text: str = ""
    power: str = ""
    toughness: str = ""
    loyalty: str = ""
    printed_name: str = ""
    printed_type_line: str = ""
    printed_text: str = ""


@dataclass(frozen=True)
class Card:
    id: str
    name: str
    layout: str
    scryfall_uri: str
    image_url: str | None
    faces: tuple[CardFace, ...] = ()
    mana_cost: str = ""
    type_line: str = ""
    oracle_text: str = ""
    flavor_text: str = ""
    power: str = ""
    toughness: str = ""
    loyalty: str = ""
    printed_name: str = ""
    printed_type_line: str = ""
    printed_text: str = ""

    language_note: str = ""
    lang: str = "en"
    set_name: str = ""
    collector_number: str = ""
    prints_search_uri: str = ""
    legalities: tuple[tuple[str, str], ...] = ()
    prices: tuple[tuple[str, str | None], ...] = ()


@dataclass(frozen=True)
class Detail:
    heading: str
    body: str


@dataclass(frozen=True)
class CardResult:
    query: CardQuery
    card: Card | None = None
    error: Literal["not_found", "ambiguous", "unavailable"] | None = None
    details: tuple[Detail, ...] = ()
    more: bool = False


@dataclass(frozen=True)
class TextResponse:
    text: str


@dataclass(frozen=True)
class ImageResponse:
    original_url: str
    preview_url: str


@dataclass(frozen=True)
class CardResponse:
    card: Card
    mode: Literal["text", "image", "prices", "rulings", "legality"] = "text"
    details: tuple[Detail, ...] = ()
    more: bool = False


Response = TextResponse | ImageResponse | CardResponse
