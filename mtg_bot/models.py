from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class CardQuery:
    name: str
    key: str
    mode: Literal["text", "image"] = "text"


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


@dataclass(frozen=True)
class CardResult:
    query: CardQuery
    card: Card | None = None
    error: Literal["not_found", "ambiguous", "unavailable"] | None = None


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
    mode: Literal["text", "image"] = "text"


Response = TextResponse | ImageResponse | CardResponse
