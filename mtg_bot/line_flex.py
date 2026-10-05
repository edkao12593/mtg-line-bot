"""LINE-specific layout; the core only supplies CardResponse/TextResponse."""

import json

from .models import CardResponse, TextResponse
from .renderer.cards import card_panels


def truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    suffix = "…（已截斷）"
    return value[: max(0, limit - len(suffix))] + suffix[:limit]


def text(value, *, size="sm", color="#253047", weight="regular", limit=1500):
    return {
        "type": "text",
        "text": truncate(value, limit),
        "size": size,
        "color": color,
        "weight": weight,
        "wrap": True,
    }


def image(url, *, small=False):
    item = {
        "type": "image",
        "url": url,
        "size": "full",
        "aspectRatio": "488:680",
        "aspectMode": "fit",
        "action": {"type": "uri", "uri": url},
    }
    if small:
        item.update(flex=0, size="100px")
    return item


def display_name(obj):
    if obj.printed_name and obj.printed_name != obj.name:
        return f"{obj.printed_name} / {obj.name}"
    return obj.name


def rules(obj, *, lang="en", use_printed=True):
    components = []
    if obj.mana_cost:
        components.append(text(obj.mana_cost, weight="bold"))
    if obj.printed_type_line or obj.type_line:
        components.append(text(obj.printed_type_line or obj.type_line, color="#596579"))
    if lang == "en":
        if obj.oracle_text:
            components.append(text(obj.oracle_text))
    elif use_printed and obj.printed_text:
        components.append(text(obj.printed_text))
    else:
        components.append(
            text("未提供此語言牌面文字，請在 Scryfall 查看。", color="#738095")
        )
    stats = f"{obj.power}/{obj.toughness}" if obj.power or obj.toughness else ""
    if obj.loyalty:
        stats += (" · " if stats else "") + "Loyalty " + obj.loyalty
    if stats:
        components.append(text(stats, weight="bold"))
    if obj.flavor_text:
        flavor = text(obj.flavor_text, color="#738095", limit=300)
        flavor["style"] = "italic"
        components.append(flavor)
    return components


def card_bubble(response: CardResponse):
    card = response.card
    title = text(display_name(card), size="lg", weight="bold", limit=200)
    title["action"] = {"type": "uri", "uri": card.scryfall_uri}
    contents = [title]
    panels = card_panels(card)
    has_image = any(url for _, url in panels)
    bubble = {"type": "bubble", "size": "mega"}
    link = card.scryfall_uri
    if response.mode in ("prices", "rulings", "legality"):
        headings = {
            "prices": "Prices for ",
            "rulings": "Rulings for ",
            "legality": "Legality for ",
        }
        title["text"] = headings[response.mode] + display_name(card)[:170]
        if response.mode == "rulings":
            link += "#rulings"
        title["action"]["uri"] = link
        if response.mode == "legality":
            labels = {
                "legal": "Legal",
                "not_legal": "Not Legal",
                "banned": "Banned",
                "restricted": "Restricted",
            }
            contents.extend(
                text(f"{fmt}: {labels.get(status, status)}", limit=300)
                for fmt, status in card.legalities
            )
            if not card.legalities:
                contents.append(text("合法性資料未提供。"))
        else:
            for detail in response.details:
                contents.append(text(detail.heading, weight="bold", limit=200))
                contents.append(text(detail.body, limit=2400))
            if not response.details:
                contents.append(
                    text(
                        "沒有裁定資料。"
                        if response.mode == "rulings"
                        else "暫無可用價格資料。"
                    )
                )
            if response.more:
                more = text(
                    "更多裁定（部分內容未顯示）"
                    if response.mode == "rulings"
                    else "更多版本價格",
                    color="#2763a5",
                )
                more["action"] = {"type": "uri", "uri": link}
                contents.append(more)
    elif response.mode == "image":
        if len(panels) == 1 and panels[0][1]:
            bubble["hero"] = image(panels[0][1])
        else:
            for index, (name, url) in enumerate(panels):
                if not card.image_url and index < len(card.faces):
                    name = display_name(card.faces[index])
                if len(panels) > 1:
                    contents.append(text(name, weight="bold", limit=200))
                contents.append(image(url) if url else text("這一面的卡圖未提供。"))
    else:
        # Split/adventure cards share one printed image but retain both rules faces.
        lang = card.requested_lang or card.lang
        use_printed = card.lang == lang
        details = rules(card, lang=lang, use_printed=use_printed)
        if card.faces and not card.oracle_text:
            details = []
            for face in card.faces:
                details += [text(display_name(face), weight="bold", limit=200)] + rules(
                    face, lang=lang, use_printed=use_printed
                )
        if not details:
            details = [text("卡牌文字未提供，請在 Scryfall 查看。")]
        thumbnails = [image(url, small=True) for _, url in panels if url]
        if thumbnails:
            contents.append(
                {
                    "type": "box",
                    "layout": "horizontal",
                    "spacing": "md",
                    "contents": [
                        {
                            "type": "box",
                            "layout": "vertical",
                            "flex": 1,
                            "spacing": "sm",
                            "contents": details,
                        },
                        {
                            "type": "box",
                            "layout": "vertical",
                            "flex": 0,
                            "spacing": "sm",
                            "contents": thumbnails,
                        },
                    ],
                }
            )
        else:
            contents.extend(details)
    if not has_image and response.mode in ("text", "image"):
        contents.append(text("卡圖未提供。", color="#a44231"))
    if card.language_note:
        contents.append(text(card.language_note, color="#738095"))
    if card.lang != "en":
        contents.append(text(f"Scryfall 語言：{card.lang}", color="#738095", limit=100))
    bubble["body"] = {
        "type": "box",
        "layout": "vertical",
        "spacing": "md",
        "contents": contents,
    }
    bubble["footer"] = {
        "type": "box",
        "layout": "vertical",
        "contents": [
            {
                "type": "button",
                "style": "link",
                "height": "sm",
                "action": {"type": "uri", "label": "在 Scryfall 查看", "uri": link},
            }
        ],
    }
    return bubble


def error_bubble(response: TextResponse):
    return {
        "type": "bubble",
        "size": "mega",
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "md",
            "contents": [
                text("查詢提示", size="lg", weight="bold"),
                text(response.text, color="#a44231"),
            ],
        },
    }


def flex_contents(responses):
    bubbles = [
        card_bubble(r) if isinstance(r, CardResponse) else error_bubble(r)
        for r in responses
    ]
    if not 1 <= len(bubbles) <= 12:
        raise ValueError("invalid bubble count")
    contents = (
        bubbles[0] if len(bubbles) == 1 else {"type": "carousel", "contents": bubbles}
    )

    # Keep unusually long Oracle text within LINE's JSON size limits.
    def shorten(node, limit):
        if isinstance(node, dict):
            if node.get("type") == "text" and "action" not in node:
                value = node["text"]
                node["text"] = truncate(value, limit)
            for child in node.values():
                shorten(child, limit)
        elif isinstance(node, list):
            for child in node:
                shorten(child, limit)

    budget = 29_000 if len(bubbles) == 1 else 49_000
    for limit in (600, 200, 100, 60):
        if len(json.dumps(contents, ensure_ascii=False).encode()) <= budget:
            break
        shorten(contents, limit)
    if len(json.dumps(contents, ensure_ascii=False).encode()) > budget:
        raise ValueError("Flex content too large")
    return contents


def alt_text(responses):
    names = [
        display_name(r.card) if isinstance(r, CardResponse) else r.text
        for r in responses
    ]
    return ("MTG 查卡：" + "、".join(names))[:400]
