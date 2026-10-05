from linebot.v3.messaging import (
    AsyncMessagingApi,
    FlexContainer,
    FlexMessage,
    ReplyMessageRequest,
    TextMessage,
)

from .line_flex import alt_text, flex_contents
from .models import CardResponse, Response, TextResponse
from .parser import parse_queries


def eligible_events(body: dict) -> list[dict]:
    """Select active text events that contain a card query or syntax error."""
    events = []
    for event in body.get("events", []):
        message = event.get("message", {})
        if event.get("type") != "message" or message.get("type") != "text":
            continue
        if event.get("mode", "active") != "active":
            continue
        text = message.get("text", "")
        parsed = parse_queries(text)
        if not parsed.queries and not parsed.notices:
            continue
        if not event.get("webhookEventId") or not event.get("replyToken"):
            continue
        events.append(
            {"id": event["webhookEventId"], "token": event["replyToken"], "text": text}
        )
    return events


def to_line_messages(responses: list[Response]):
    card_count = sum(isinstance(r, CardResponse) for r in responses)
    if card_count >= 2 or len(responses) > 5:
        if not any(isinstance(r, CardResponse) for r in responses):
            # An all-error reply needs one concise text message.
            if all(isinstance(r, TextResponse) for r in responses):
                return [TextMessage(text="\n".join(r.text for r in responses)[:4500])]
            raise ValueError("too many LINE messages")
        if not all(isinstance(r, (CardResponse, TextResponse)) for r in responses):
            raise ValueError("cannot combine these responses")
        return [
            FlexMessage(
                alt_text=alt_text(responses),
                contents=FlexContainer.from_dict(flex_contents(responses)),
            )
        ]
    messages = []
    for response in responses:
        if isinstance(response, TextResponse):
            messages.append(TextMessage(text=response.text))
        elif isinstance(response, CardResponse):
            messages.append(
                FlexMessage(
                    alt_text=alt_text([response]),
                    contents=FlexContainer.from_dict(flex_contents([response])),
                )
            )
        else:
            raise TypeError("unknown response")
    return messages


class LineReplySender:
    def __init__(self, api: AsyncMessagingApi):
        self.api = api

    async def reply(self, token: str, responses: list[Response]):
        if responses:
            await self.api.reply_message(
                ReplyMessageRequest(
                    reply_token=token, messages=to_line_messages(responses)
                ),
                _request_timeout=8,
            )
