import asyncio
import time
from dataclasses import replace
from urllib.parse import urlparse, quote, parse_qsl
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
    def fields(obj):
        return {key: str(obj.get(key) or "") for key in (
            "mana_cost", "type_line", "oracle_text", "flavor_text", "power", "toughness", "loyalty",
            "printed_name", "printed_type_line", "printed_text"
        )}
    return Card(data["id"], data["name"], data["layout"], data["scryfall_uri"], image(data),
                tuple(CardFace(f["name"], image(f), **fields(f)) for f in data.get("card_faces", [])),
                **fields(data), lang=str(data.get("lang") or "en"), set_name=str(data.get("set_name") or ""),
                collector_number=str(data.get("collector_number") or ""),
                prints_search_uri=str(data.get("prints_search_uri") or ""),
                legalities=tuple((str(k), str(v)) for k, v in (data.get("legalities") or {}).items()),
                prices=tuple((str(k), None if v is None else str(v)) for k, v in (data.get("prices") or {}).items()))



class ScryfallClient:
    def __init__(self, http: httpx.AsyncClient, *, user_agent: str, limiter: RateLimiter | None = None):
        self.http, self.limiter = http, limiter or RateLimiter()
        self.headers = {"User-Agent": user_agent, "Accept": "application/json"}

    async def get_card_by_name(self, name: str, set_code: str | None = None) -> Card:
        params = {"fuzzy": name}
        if set_code:
            params["set"] = set_code
        return self._decode(await self._request("https://api.scryfall.com/cards/named", params=params))

    async def get_card_by_collector(self, set_code: str, number: str, name: str | None = None) -> Card:
        url = f"https://api.scryfall.com/cards/{quote(set_code, safe='')}/{quote(number, safe='')}"
        # Set+number determines identity; the name only hints at language.
        card = self._decode(await self._request(url))
        if not name:
            return card
        try:
            named = await self.get_card_by_name(name)
        except ScryfallError:
            return card
        if named.lang == card.lang:
            return card
        try:
            return self._decode(await self._request(url + "/" + quote(named.lang, safe='')))
        except ScryfallError as error:
            if error.kind == "not_found":
                return replace(card, requested_lang=named.lang, language_note=f"Scryfall 未收錄此版本的 {named.lang} 資料；顯示預設版本。")
            return replace(card, requested_lang=named.lang, language_note="此語言版本暫時無法取得；顯示預設版本。")

    def _decode(self, data: dict) -> Card:
        try:
            return decode_card(data)
        except (ValueError, KeyError, TypeError):
            raise ScryfallError("unavailable") from None

    async def get_rulings(self, card: Card) -> tuple[dict, ...]:
        data = await self._request(f"https://api.scryfall.com/cards/{quote(card.id, safe='')}/rulings")
        try:
            rows = data["data"]
            if not isinstance(rows, list) or any(not isinstance(r, dict) or not isinstance(r.get("comment"), str) or not isinstance(r.get("published_at"), str) for r in rows):
                raise ValueError()
            return tuple(rows)
        except (KeyError, ValueError, TypeError):
            raise ScryfallError("unavailable") from None

    async def get_price_prints(self, card: Card) -> tuple[tuple[Card, ...], bool]:
        # Only one API page; never fetch every print of a frequently reprinted card.
        data = await self._request(card.prints_search_uri, params={"order": "released", "dir": "desc"})
        try:
            prints = tuple(decode_card(row) for row in data["data"])
            priced = tuple(p for p in prints if any(value is not None for _, value in p.prices))
            return priced[:10], bool(data.get("has_more")) or len(priced) > 10
        except (KeyError, ValueError, TypeError):
            raise ScryfallError("unavailable") from None

    async def _request(self, url: str, params: dict | None = None) -> dict:
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname != "api.scryfall.com" or parsed.username or parsed.password or parsed.port not in (None, 443):
            raise ScryfallError("unavailable")
        params = {**dict(parse_qsl(parsed.query)), **(params or {})}
        for attempt in range(3):
            await self.limiter.acquire()
            try:
                r = await self.http.get(url, params=params, headers=self.headers)
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
                data = r.json()
                if not isinstance(data, dict):
                    raise ValueError()
                return data
            except (ValueError, KeyError, TypeError):
                raise ScryfallError("unavailable") from None
        raise ScryfallError("unavailable")
