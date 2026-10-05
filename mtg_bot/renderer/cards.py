from dataclasses import replace
from urllib.parse import urlsplit, urlunsplit

from ..models import Card, CardResponse, CardResult, Response, TextResponse


def clean_card_link(url: str) -> str:
    parts = urlsplit(url)
    if (
        parts.scheme != "https"
        or parts.hostname != "scryfall.com"
        or parts.username
        or parts.password
    ):
        raise ValueError("invalid Scryfall card link")
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def card_panels(card: Card) -> list[tuple[str, str | None]]:
    """Whole-card image first (normal/split/adventure/flip); otherwise all faces."""
    if card.image_url:
        return [(card.name, card.image_url)]
    if card.faces:
        return [(f.name, f.image_url) for f in card.faces]
    return [(card.name, None)]


class CardRenderer:
    """Platform-neutral card content. The adapter chooses its presentation."""

    async def render(
        self, results: list[CardResult], notices: tuple[str, ...] = ()
    ) -> list[Response]:
        responses: list[Response] = []
        for result in results:
            if result.card and not result.error:
                card = replace(
                    result.card, scryfall_uri=clean_card_link(result.card.scryfall_uri)
                )
                responses.append(
                    CardResponse(card, result.query.mode, result.details, result.more)
                )
            else:
                reason = {
                    "not_found": "查不到卡",
                    "ambiguous": "有多張卡符合，請輸入更完整的卡名",
                    "unavailable": "查詢暫時失敗，請稍後再試",
                }.get(result.error, "查詢暫時失敗")
                label = result.query.name
                if result.query.set_code:
                    label += f"|{result.query.set_code}"
                if result.query.collector_number:
                    label += f"|{result.query.collector_number}"
                if result.error == "not_found" and result.query.set_code:
                    reason = "指定系列或版本中查不到卡"
                responses.append(TextResponse(f"{label}：{reason}。"))
        if notices:
            responses.append(TextResponse("\n".join(notices)[:4500]))
        return responses
