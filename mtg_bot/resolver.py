import asyncio
from typing import Awaitable, Callable, Protocol, TypeVar

from .cache import TTLCache
from .models import Card, CardQuery, CardResult, Detail
from .scryfall import ScryfallError

T = TypeVar("T")


class CardLookup(Protocol):
    async def get_card_by_name(
        self, name: str, set_code: str | None = None
    ) -> Card: ...
    async def get_card_by_collector(
        self, set_code: str, number: str, name: str | None = None
    ) -> Card: ...
    async def get_rulings(self, card: Card) -> tuple[dict, ...]: ...
    async def get_price_prints(self, card: Card) -> tuple[tuple[Card, ...], bool]: ...


class Resolver:
    def __init__(self, client: CardLookup):
        self.client = client
        self.cache: TTLCache[tuple[Card | None, str | None]] = TTLCache(1024)
        self.inflight: dict[str, asyncio.Task] = {}
        self.detail_cache = TTLCache(512)
        self.detail_inflight: dict[str, asyncio.Task] = {}
        self.semaphore = asyncio.Semaphore(4)

    async def _fetch(self, query: CardQuery):
        try:
            async with self.semaphore:
                if query.collector_number:
                    card = await self.client.get_card_by_collector(
                        query.set_code, query.collector_number, query.name
                    )
                elif query.set_code:
                    card = await self.client.get_card_by_name(
                        query.name, query.set_code
                    )
                else:
                    card = await self.client.get_card_by_name(query.name)
            value = (card, None)
            self.cache.put(query.key, value, 3600)
        except ScryfallError as e:
            value = (None, e.kind)
            if e.kind in ("not_found", "ambiguous"):
                self.cache.put(query.key, value, 60)
        return value

    async def resolve(self, query: CardQuery) -> CardResult:
        value = self.cache.get(query.key)
        if value is None:
            task = self._shared_task(
                self.inflight, query.key, lambda: self._fetch(query)
            )
            try:
                value = await asyncio.shield(task)
            except Exception:
                value = (None, "unavailable")
        result = CardResult(query, value[0], value[1])
        if result.card and query.mode in ("prices", "rulings"):
            try:
                details, more = await self._details(result.card, query.mode)
                return CardResult(query, result.card, details=details, more=more)
            except Exception:
                return CardResult(query, result.card, "unavailable")
        return result

    async def _fetch_details(self, card: Card, mode: str):
        async with self.semaphore:
            if mode == "rulings":
                rows = await self.client.get_rulings(card)
                details, used = [], 0
                for row in rows:
                    body = row["comment"]
                    if len(details) >= 8 or used + len(body) > 2400:
                        if not details:
                            details.append(
                                Detail(row["published_at"], body[:2300] + "…（已截斷）")
                            )
                        break
                    details.append(Detail(row["published_at"], body))
                    used += len(body)
                more = len(details) < len(rows) or any(
                    "（已截斷）" in d.body for d in details
                )
                value = (tuple(details), more)
            else:
                prints, more = await self.client.get_price_prints(card)
                labels = {
                    "usd": "USD",
                    "usd_foil": "USD foil",
                    "usd_etched": "USD etched",
                    "eur": "EUR",
                    "eur_foil": "EUR foil",
                    "eur_etched": "EUR etched",
                    "tix": "TIX",
                }
                details = tuple(
                    Detail(
                        f"{p.set_name} · #{p.collector_number}",
                        " • ".join(
                            f"{labels.get(k, k)} {v}"
                            for k, v in p.prices
                            if v is not None
                        ),
                    )
                    for p in prints
                )
                value = (details, more)
        self.detail_cache.put(
            f"{mode}:{card.id}", value, 900 if mode == "prices" else 86400
        )
        return value

    async def _details(self, card: Card, mode: str):
        key = f"{mode}:{card.id}"
        cached = self.detail_cache.get(key)
        if cached is not None:
            return cached
        task = self._shared_task(
            self.detail_inflight, key, lambda: self._fetch_details(card, mode)
        )
        return await asyncio.shield(task)

    @staticmethod
    def _shared_task(
        pending: dict[str, asyncio.Task], key: str, fetch: Callable[[], Awaitable[T]]
    ) -> asyncio.Task[T]:
        task = pending.get(key)
        if task is None:
            task = asyncio.create_task(fetch())
            pending[key] = task

            def done(completed):
                if pending.get(key) is completed:
                    pending.pop(key, None)
                # A request may finish after all callers time out; consume its error.
                if not completed.cancelled():
                    completed.exception()

            task.add_done_callback(done)
        return task

    async def resolve_many(self, queries: tuple[CardQuery, ...]) -> list[CardResult]:
        return list(await asyncio.gather(*(self.resolve(q) for q in queries)))

    async def close(self):
        tasks = list(self.inflight.values()) + list(self.detail_inflight.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
