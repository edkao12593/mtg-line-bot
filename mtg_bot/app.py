import asyncio
import json
import logging
import os
import time
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from linebot.v3.messaging import AsyncApiClient, AsyncMessagingApi, Configuration
from linebot.v3.webhook import SignatureValidator

from .line_adapter import LineReplySender, eligible_events
from .models import TextResponse
from .renderer.cards import CardRenderer
from .resolver import Resolver
from .scryfall import ScryfallClient
from .service import LookupService
from .storage import Inbox, InboxFull

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Settings:
    secret: str
    token: str
    user_agent: str
    data_dir: Path = Path("data")

    def __post_init__(self):
        if not self.secret or not self.token:
            raise ValueError("LINE credentials required")
        if not self.user_agent:
            raise ValueError("descriptive SCRYFALL_USER_AGENT required")

    @classmethod
    def from_env(cls):
        return cls(
            os.environ["LINE_CHANNEL_SECRET"],
            os.environ["LINE_CHANNEL_ACCESS_TOKEN"],
            os.environ["SCRYFALL_USER_AGENT"],
            Path(os.getenv("DATA_DIR", "data")),
        )


async def worker(inbox: Inbox, service, sender):
    while True:
        row = inbox.claim()
        if row is None:
            await asyncio.sleep(0.1)
            continue
        event_id, token, text, received = row
        remaining = 45 - (time.time() - received)
        if remaining <= 0:
            inbox.finish(event_id, "expired")
            continue
        state = "failed"
        try:
            try:
                async with asyncio.timeout(min(25, remaining)):
                    responses = await service.handle_text(text)
            except TimeoutError:
                responses = [TextResponse("查詢逾時，請稍後再試。")]
            except Exception:
                log.warning("card processing failed")
                responses = [TextResponse("查詢暫時失敗，請稍後再試。")]
            if time.time() - received < 48:
                # A failed reply could still have reached LINE. Do not retry it.
                state = "uncertain"
                await sender.reply(token, responses)
                state = "sent"
            else:
                state = "expired"
        except asyncio.CancelledError:
            state = "uncertain"
            raise
        except Exception:
            log.warning("LINE reply failed; delivery may be uncertain")
        finally:
            inbox.finish(event_id, state)


def create_app(
    settings: Settings | None = None,
    *,
    service=None,
    sender=None,
    start_workers: bool = True,
) -> FastAPI:
    settings = settings or Settings.from_env()
    inbox = Inbox(settings.data_dir / "inbox.sqlite3")
    validator = SignatureValidator(settings.secret)

    @asynccontextmanager
    async def lifespan(app):
        nonlocal service, sender
        async with AsyncExitStack() as resources:
            resolver = None
            if start_workers and service is None:
                http = await resources.enter_async_context(
                    httpx.AsyncClient(
                        timeout=httpx.Timeout(8),
                        limits=httpx.Limits(max_connections=12),
                    )
                )
                resolver = Resolver(
                    ScryfallClient(http, user_agent=settings.user_agent)
                )
                service = LookupService(resolver, CardRenderer())
            if start_workers and sender is None:
                sdk = AsyncApiClient(Configuration(access_token=settings.token))
                resources.push_async_callback(sdk.close)
                sender = LineReplySender(AsyncMessagingApi(sdk))
            tasks = (
                [asyncio.create_task(worker(inbox, service, sender)) for _ in range(2)]
                if start_workers
                else []
            )
            try:
                yield
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                if resolver:
                    await resolver.close()
                inbox.close()

    app = FastAPI(lifespan=lifespan)
    app.state.inbox = inbox

    @app.get("/healthz")
    async def health():
        return {"status": "ok"}

    @app.post("/webhook")
    async def webhook(request: Request):
        parts, size = [], 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > 1_000_000:
                raise HTTPException(413, "payload too large")
            parts.append(chunk)
        raw = b"".join(parts)
        try:
            body_text = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise HTTPException(400, "invalid encoding") from None
        # Validate the exact received body, before JSON parsing or normalization.
        if not validator.validate(
            body_text, request.headers.get("x-line-signature", "")
        ):
            raise HTTPException(400, "invalid signature")
        try:
            body = json.loads(body_text)
            if not isinstance(body, dict) or not isinstance(
                body.get("events", []), list
            ):
                raise ValueError()
            events = eligible_events(body)
        except (ValueError, TypeError, AttributeError):
            raise HTTPException(400, "invalid payload") from None
        try:
            inbox.enqueue(events)
        except InboxFull:
            raise HTTPException(503, "busy; retry later") from None
        return {"ok": True}

    return app
