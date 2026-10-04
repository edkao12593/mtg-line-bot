import asyncio
from typing import Protocol
from .models import Card, CardQuery, CardResult
from .cache import TTLCache
from .scryfall import ScryfallError


class CardLookup(Protocol):
    async def get_card_by_name(self, name: str) -> Card: ...


class Resolver:
    def __init__(self, client: CardLookup):
        self.client = client
        self.cache: TTLCache[tuple[Card | None, str | None]] = TTLCache(1024)
        self.inflight: dict[str, asyncio.Task] = {}
        self.semaphore = asyncio.Semaphore(4)

    async def _fetch(self, query: CardQuery):
        try:
            async with self.semaphore:
                card = await self.client.get_card_by_name(query.name)
            value = (card, None)
            self.cache.put(query.key, value, 86400)
        except ScryfallError as e:
            value = (None, e.kind)
            if e.kind in ("not_found", "ambiguous"):
                self.cache.put(query.key, value, 60)
        return value

    async def resolve(self, query: CardQuery) -> CardResult:
        value = self.cache.get(query.key)
        if value is None:
            task = self.inflight.get(query.key)
            if task is None:
                task = asyncio.create_task(self._fetch(query))
                self.inflight[query.key] = task
                def done(completed):
                    if self.inflight.get(query.key) is completed:
                        self.inflight.pop(query.key, None)
                    if not completed.cancelled():
                        completed.exception()  # consume errors if all waiters timed out
                task.add_done_callback(done)
            try:
                value = await asyncio.shield(task)
            except Exception:
                value = (None, "unavailable")
        return CardResult(query, value[0], value[1])

    async def resolve_many(self, queries: tuple[CardQuery, ...]) -> list[CardResult]:
        return list(await asyncio.gather(*(self.resolve(q) for q in queries)))

    async def close(self):
        tasks = list(self.inflight.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
