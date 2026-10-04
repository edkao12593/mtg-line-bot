import asyncio
import time
from urllib.parse import urlparse
import httpx
from .models import Card, CardFace


class ScryfallError(Exception):
    def __init__(self, kind: str):
        super().__init__(kind)
        self.kind = kind


class RateLimiter:
    """One shared start-time gate: no burst, all retries pass through it."""
    def __init__(self, interval: float = 0.12, clock=time.monotonic, sleep=asyncio.sleep):
        self.interval, self.clock, self.sleep = interval, clock, sleep
        self.lock = asyncio.Lock()
        self.next_at = 0.0

    async def acquire(self):
        async with self.lock:
            await self.sleep(max(0.0, self.next_at - self.clock()))
            self.next_at = self.clock() + self.interval

    async def cooldown(self, seconds: float):
        async with self.lock:
            self.next_at = max(self.next_at, self.clock() + seconds)


def safe_image_url(value: str | None) -> str | None:
    if not value:
        return None
    u = urlparse(value)
    host = u.hostname or ""
    if u.scheme == "https" and (host == "scryfall.io" or host.endswith(".scryfall.io")) and not u.username and not u.password and u.port in (None, 443):
        return value
    return None


def decode_card(data: dict) -> Card:
    def image(obj):
        return safe_image_url((obj.get("image_uris") or {}).get("normal"))
    return Card(data["id"], data["name"], data["layout"], data["scryfall_uri"], image(data),
                tuple(CardFace(f["name"], image(f)) for f in data.get("card_faces", [])))


class ScryfallClient:
    def __init__(self, http: httpx.AsyncClient, *, user_agent: str, limiter: RateLimiter | None = None):
        self.http, self.limiter = http, limiter or RateLimiter()
        self.headers = {"User-Agent": user_agent, "Accept": "application/json"}

    async def get_card_by_name(self, name: str) -> Card:
        """MVP fuzzy lookup. Does not silently select from ambiguous names."""
        for attempt in range(3):
            await self.limiter.acquire()
            try:
                r = await self.http.get("https://api.scryfall.com/cards/named", params={"fuzzy": name}, headers=self.headers)
            except httpx.TransportError:
                if attempt == 2:
                    raise ScryfallError("unavailable") from None
                await asyncio.sleep(0.25 * 2 ** attempt)
                continue
            if r.status_code == 429:
                try:
                    delay = max(2.0, float(r.headers.get("Retry-After", "2")))
                except ValueError:
                    delay = 2.0
                await self.limiter.cooldown(delay)
                # Do not retry 429 and power through a hard rate limit.
                raise ScryfallError("unavailable")
            if r.status_code in (500, 502, 503, 504) and attempt < 2:
                await asyncio.sleep(0.25 * 2 ** attempt)
                continue
            if r.status_code == 404:
                try:
                    ambiguous = r.json().get("type") == "ambiguous"
                except ValueError:
                    ambiguous = False
                raise ScryfallError("ambiguous" if ambiguous else "not_found")
            if r.status_code != 200:
                raise ScryfallError("unavailable")
            try:
                return decode_card(r.json())
            except (ValueError, KeyError, TypeError):
                raise ScryfallError("unavailable") from None
        raise ScryfallError("unavailable")
