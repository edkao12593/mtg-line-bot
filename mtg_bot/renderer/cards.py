from dataclasses import replace
from urllib.parse import urlsplit, urlunsplit

from ..models import CardResult, CardResponse, Response, TextResponse


def clean_card_link(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname != "scryfall.com" or parts.username or parts.password:
        raise ValueError("invalid Scryfall card link")
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


class CardRenderer:
    """Platform-neutral card content. The adapter chooses its presentation."""

    async def render(self, results: list[CardResult], notices: tuple[str, ...] = ()) -> list[Response]:
        responses: list[Response] = []
        for result in results:
            if result.card:
                card = replace(result.card, scryfall_uri=clean_card_link(result.card.scryfall_uri))
                responses.append(CardResponse(card, result.query.mode))
            else:
                reason = {
                    "not_found": "查不到卡",
                    "ambiguous": "有多張卡符合，請輸入更完整的卡名",
                    "unavailable": "查詢暫時失敗，請稍後再試",
                }.get(result.error, "查詢暫時失敗")
                responses.append(TextResponse(f"{result.query.name}：{reason}。"))
        if notices:
            responses.append(TextResponse("\n".join(notices)[:4500]))
        return responses
