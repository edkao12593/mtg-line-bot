import io
import math
from PIL import Image, ImageOps, ImageDraw

CELL = (488, 680)


def grid_shape(count: int) -> tuple[int, int]:
    if count < 1:
        raise ValueError("empty grid")
    columns = 1 if count == 1 else 2 if count in (2, 4) else 3
    return columns, math.ceil(count / columns)


def compose(images: list[bytes | None], labels: list[str]) -> Image.Image:
    columns, rows = grid_shape(len(images))
    canvas = Image.new("RGB", (columns * CELL[0], rows * CELL[1]), "#16191f")
    for i, data in enumerate(images):
        x, y = (i % columns) * CELL[0], (i // columns) * CELL[1]
        if data is not None:
            with Image.open(io.BytesIO(data)) as source:
                tile = ImageOps.contain(source.convert("RGB"), (CELL[0] - 12, CELL[1] - 30))
            canvas.paste(tile, (x + (CELL[0] - tile.width) // 2, y + 4))
        else:
            ImageDraw.Draw(canvas).text((x + 18, y + 100), "Image unavailable", fill="white")
        # Labels from Scryfall are English in MVP. Truncate for fixed cell width.
        ImageDraw.Draw(canvas).text((x + 8, y + CELL[1] - 24), labels[i][:65], fill="white")
    return canvas


def encode_jpeg(image: Image.Image, *, preview: bool = False) -> bytes:
    image = image.copy()
    if preview:
        image.thumbnail((1000, 1000))
    out = io.BytesIO()
    image.save(out, "JPEG", quality=85, optimize=True)
    data = out.getvalue()
    if len(data) >= (1_000_000 if preview else 10_000_000):
        raise ValueError("image exceeds LINE limit")
    return data
