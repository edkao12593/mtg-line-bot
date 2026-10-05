from .models import Response
from .parser import parse_queries
from .renderer.cards import CardRenderer
from .resolver import Resolver


class LookupService:
    def __init__(self, resolver: Resolver, renderer: CardRenderer):
        self.resolver, self.renderer = resolver, renderer

    async def handle_text(self, text: str) -> list[Response]:
        parsed = parse_queries(text)
        if not parsed.queries and not parsed.notices:
            return []
        results = await self.resolver.resolve_many(parsed.queries)
        return await self.renderer.render(results, parsed.notices)
