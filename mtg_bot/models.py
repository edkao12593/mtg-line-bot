from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class CardQuery:
    name: str
    key: str
    mode: Literal["image", "image_alias"] = "image"


@dataclass(frozen=True)
class ParseResult:
    queries: tuple[CardQuery, ...]
    notices: tuple[str, ...] = ()


@dataclass(frozen=True)
class CardFace:
    name: str
    image_url: str | None


@dataclass(frozen=True)
class Card:
    id: str
    name: str
    layout: str
    scryfall_uri: str
    image_url: str | None
    faces: tuple[CardFace, ...] = ()


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


Response = TextResponse | ImageResponse
