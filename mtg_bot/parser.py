import re
import unicodedata

from .models import CardQuery, ParseResult

PATTERN = re.compile(r"\[\[([^\[\]]+)\]\]")


def normalize(name: str) -> str:
    return " ".join(unicodedata.normalize("NFC", name).split()).casefold()


def parse_queries(
    text: str, *, max_queries: int = 9, max_name_length: int = 200
) -> ParseResult:
    """Global matches, first occurrence order, case/whitespace-insensitive dedup.

    The first prefix selects the response mode; set and collector select a print.
    """
    if max_queries < 1:
        raise ValueError("max_queries must be positive")
    queries, notices, seen = [], [], set()
    for match in PATTERN.finditer(text):
        raw = match.group(1).strip()
        prefixes = {"!": "image", "$": "prices", "?": "rulings", "#": "legality"}
        mode = prefixes.get(raw[:1], "text")
        raw = raw.lstrip("!$?#").strip()
        if not raw:
            continue
        parts = [" ".join(part.split()) for part in raw.split("|")]
        if len(parts) > 3 or not parts[0] or (len(parts) == 3 and not parts[1]):
            notices.append("指定版本格式錯誤；請使用 [[Card|SET|NUM]]。")
            continue
        name = parts[0]
        set_code = parts[1].casefold() if len(parts) > 1 and parts[1] else None
        number = parts[2].casefold() if len(parts) > 2 and parts[2] else None
        if len(name) > max_name_length:
            notices.append("卡名太長，已略過。")
            continue
        if (set_code and not re.fullmatch(r"[a-z0-9]{1,12}", set_code)) or (
            number and not re.fullmatch(r"[\w★.-]{1,30}", number)
        ):
            notices.append("系列或收藏編號格式錯誤。")
            continue
        key = (
            f"print:{set_code}:{number}:{normalize(name)}"
            if number
            else f"named:{normalize(name)}:{set_code}"
            if set_code
            else normalize(name)
        )
        display_key = (key, mode)
        if display_key in seen:
            continue
        seen.add(display_key)
        if len(queries) >= max_queries:
            notices.append(f"每則最多查 {max_queries} 張；超出部分已略過。")
            break
        queries.append(CardQuery(name, key, mode, set_code, number))
    return ParseResult(tuple(queries), tuple(dict.fromkeys(notices)))
