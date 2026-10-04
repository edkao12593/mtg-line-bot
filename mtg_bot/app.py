import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
import re
import time
from urllib.parse import urlparse
import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from linebot.v3.webhook import SignatureValidator
from linebot.v3.messaging import AsyncApiClient, AsyncMessagingApi, Configuration
from .line_adapter import eligible_events, LineReplySender
from .models import TextResponse
from .renderer.cards import CardRenderer
from .resolver import Resolver
from .scryfall import ScryfallClient
from .service import LookupService
from .storage import Inbox, InboxFull, LocalImageStore

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Settings:
    secret: str
    token: str
    base_url: str
    user_agent: str
    data_dir: Path = Path("data")

    def __post_init__(self):
        u = urlparse(self.base_url)
        if not self.secret or not self.token:
            raise ValueError("LINE credentials required")
        if u.scheme != "https" or not u.hostname or u.username or u.password or u.query or u.fragment:
            raise ValueError("PUBLIC_BASE_URL must be a public HTTPS base URL")
        if not self.user_agent:
            raise ValueError("descriptive SCRYFALL_USER_AGENT required")

    @classmethod
    def from_env(cls):
        return cls(os.environ["LINE_CHANNEL_SECRET"], os.environ["LINE_CHANNEL_ACCESS_TOKEN"], os.environ["PUBLIC_BASE_URL"], os.environ["SCRYFALL_USER_AGENT"], Path(os.getenv("DATA_DIR", "data")))


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
                log.warning("card processing failed")  # no full payload/token logging
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


def create_app(settings: Settings | None = None, *, service=None, sender=None, start_workers: bool = True) -> FastAPI:
    settings = settings or Settings.from_env()
    inbox = Inbox(settings.data_dir / "inbox.sqlite3")
    validator = SignatureValidator(settings.secret)

    @asynccontextmanager
    async def lifespan(app):
        nonlocal service, sender
        http = httpx.AsyncClient(timeout=httpx.Timeout(8), limits=httpx.Limits(max_connections=12))
        sdk = AsyncApiClient(Configuration(access_token=settings.token))
        resolver = None
        if service is None:
            resolver = Resolver(ScryfallClient(http, user_agent=settings.user_agent))
            service = LookupService(resolver, CardRenderer())
        if sender is None:
            sender = LineReplySender(AsyncMessagingApi(sdk))
        tasks = [asyncio.create_task(worker(inbox, service, sender)) for _ in range(2)] if start_workers else []
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if resolver:
                await resolver.close()
            await sdk.close()
            await http.aclose()
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
        if not validator.validate(body_text, request.headers.get("x-line-signature", "")):
            raise HTTPException(400, "invalid signature")
        try:
            body = json.loads(body_text)
            if not isinstance(body, dict) or not isinstance(body.get("events", []), list):
                raise ValueError()
            events = eligible_events(body)
        except (ValueError, TypeError, AttributeError):
            raise HTTPException(400, "invalid payload") from None
        try:
            inbox.enqueue(events)
        except InboxFull:
            raise HTTPException(503, "busy; retry later") from None
        return {"ok": True}

    @app.get("/images/{name}")
    async def image(name: str):
        if not re.fullmatch(r"[0-9a-f]{64}\.jpg", name):
            raise HTTPException(404)
        path = settings.data_dir / "images" / name
        if not path.is_file() or time.time() - path.stat().st_mtime > 7 * 86400:
            raise HTTPException(404)
        # A single card could be PNG from upstream; detect its actual encoding.
        with path.open("rb") as source:
            media_type = "image/png" if source.read(8) == b"\x89PNG\r\n\x1a\n" else "image/jpeg"
        return FileResponse(path, media_type=media_type, headers={"Cache-Control": "public, max-age=86400"})

    return app
