import asyncio
import io
import logging
from typing import Protocol
import httpx
from PIL import Image
from ..cache import TTLCache
from ..models import Card, CardResult, ImageResponse, TextResponse, Response
from ..scryfall import safe_image_url
from .grid import compose, encode_jpeg


log = logging.getLogger(__name__)


class ImagePublisher(Protocol):
    async def publish(self, original: bytes, preview: bytes) -> ImageResponse: ...


def card_panels(card: Card) -> list[tuple[str, str | None]]:
    """Whole-card image first (normal/split/adventure/flip); otherwise all faces."""
    if card.image_url:
        return [(card.name, card.image_url)]
    if card.faces:
        return [(f.name, f.image_url) for f in card.faces]
    return [(card.name, None)]


class ImageRenderer:
    def __init__(self, http: httpx.AsyncClient, publisher: ImagePublisher):
        self.http, self.publisher = http, publisher
        self.cache: TTLCache[bytes] = TTLCache(64)
        self.semaphore = asyncio.Semaphore(4)

    async def download(self, url: str | None) -> bytes | None:
        if not url or not safe_image_url(url):
            return None
        cached = self.cache.get(url)
        if cached is not None:
            return cached
        for attempt in range(2):
            try:
                async with self.semaphore:
                    headers = {"User-Agent": "mtg-line-bot/0.1", "Accept": "image/jpeg,image/png"}
                    async with self.http.stream("GET", url, headers=headers, follow_redirects=False) as r:
                        if r.status_code != 200:
                            log.warning("card image HTTP status=%s attempt=%s", r.status_code, attempt + 1)
                            if r.status_code in (500, 502, 503, 504) and attempt == 0:
                                await asyncio.sleep(0.3)
                                continue
                            return None
                        chunks, size = [], 0
                        async for chunk in r.aiter_bytes():
                            size += len(chunk)
                            if size > 1_000_000:
                                log.warning("card image exceeds download size limit")
                                return None
                            chunks.append(chunk)
                        data = b"".join(chunks)
                def validate():
                    with Image.open(io.BytesIO(data)) as img:
                        if img.width * img.height > 4_000_000 or img.format not in ("JPEG", "PNG"):
                            raise ValueError("unsupported image")
                        img.load()
                await asyncio.to_thread(validate)
                self.cache.put(url, data, 86400)
                return data
            except httpx.TransportError as exc:
                log.warning("card image transport error=%s attempt=%s", type(exc).__name__, attempt + 1)
                if attempt == 0:
                    await asyncio.sleep(0.3)
                    continue
            except (httpx.HTTPError, OSError, ValueError, Image.DecompressionBombError) as exc:
                log.warning("card image download/validation error=%s", type(exc).__name__)
                return None
        return None

    async def render(self, results: list[CardResult], notices: tuple[str, ...] = ()) -> list[Response]:
        errors = list(notices)
        panels = []
        for result in results:
            if result.card:
                panels.extend(card_panels(result.card))
            else:
                message = {"not_found": "查不到卡", "ambiguous": "名稱不夠明確", "unavailable": "查詢暫時失敗"}.get(result.error, "查詢暫時失敗")
                errors.append(f"{result.query.name}：{message}。")
        responses: list[Response] = []
        if panels:
            images = await asyncio.gather(*(self.download(url) for _, url in panels))
            if any(data is not None for data in images):
                def build():
                    if len(images) == 1 and images[0] is not None:
                        with Image.open(io.BytesIO(images[0])) as source:
                            preview = encode_jpeg(source.convert("RGB"), preview=True)
                        return images[0], preview  # preserve single original card image
                    canvas = compose(images, [name for name, _ in panels])
                    return encode_jpeg(canvas), encode_jpeg(canvas, preview=True)
                original, preview = await asyncio.to_thread(build)
                responses.append(await self.publisher.publish(original, preview))
            missing = [name for (name, _), data in zip(panels, images) if data is None]
            errors.extend(f"{name}：卡圖暫時無法取得。" for name in missing)
        links = [f"{result.card.name}\n{result.card.scryfall_uri}" for result in results if result.card]
        text = "\n\n".join(links)
        if errors:
            text += ("\n\n" if text else "") + "\n".join(errors)
        if text:
            responses.append(TextResponse(text[:4500]))
        return responses
