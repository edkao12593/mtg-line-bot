from linebot.v3.messaging import AsyncMessagingApi, ReplyMessageRequest, ImageMessage, TextMessage
from .models import ImageResponse, TextResponse, Response
from .parser import parse_queries


def eligible_events(body: dict) -> list[dict]:
    """Join/follow/non-text/ordinary conversation are silent."""
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
        events.append({"id": event["webhookEventId"], "token": event["replyToken"], "text": text})
    return events


def to_line_messages(responses: list[Response]):
    if len(responses) > 5:
        raise ValueError("too many LINE messages")
    messages = []
    for response in responses:
        if isinstance(response, ImageResponse):
            messages.append(ImageMessage(original_content_url=response.original_url, preview_image_url=response.preview_url))
        elif isinstance(response, TextResponse):
            messages.append(TextMessage(text=response.text))
        else:
            raise TypeError("unknown response")
    return messages


class LineReplySender:
    def __init__(self, api: AsyncMessagingApi):
        self.api = api

    async def reply(self, token: str, responses: list[Response]):
        if responses:
            await self.api.reply_message(ReplyMessageRequest(reply_token=token, messages=to_line_messages(responses)), _request_timeout=8)
