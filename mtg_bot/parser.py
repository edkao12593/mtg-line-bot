import re
import unicodedata
from .models import CardQuery, ParseResult

PATTERN = re.compile(r"\[\[([^\[\]]+)\]\]")


def normalize(name: str) -> str:
    return " ".join(unicodedata.normalize("NFC", name).split()).casefold()


def parse_queries(text: str, *, max_queries: int = 9, max_name_length: int = 200) -> ParseResult:
    """Global matches, first occurrence order, case/whitespace-insensitive dedup.

    '!' requests a large image. Set/search syntax is intentionally unsupported.
    """
    if max_queries < 1:
        raise ValueError("max_queries must be positive")
    queries, notices, seen = [], [], set()
    for match in PATTERN.finditer(text):
        raw = match.group(1).strip()
        mode = "image" if raw.startswith("!") else "text"
        name = " ".join((raw[1:] if raw.startswith("!") else raw).split())
        if not name:
            continue
        if len(name) > max_name_length:
            notices.append("卡名太長，已略過。")
            continue
        if "|" in name:
            notices.append("MVP 暫不支援指定版本；請使用 [[Card Name]]。")
            continue
        key = normalize(name)
        display_key = (key, mode)
        if display_key in seen:
            continue
        seen.add(display_key)
        if len(queries) >= max_queries:
            notices.append(f"每則最多查 {max_queries} 張；超出部分已略過。")
            break
        queries.append(CardQuery(name, key, mode))
    return ParseResult(tuple(queries), tuple(dict.fromkeys(notices)))
