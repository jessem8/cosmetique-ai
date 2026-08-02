from __future__ import annotations

import io

from PIL import Image

from app.services.enhancements import premium_finish


def _poster() -> bytes:
    image = Image.new("RGB", (1080, 1080), (230, 240, 238))
    for x in range(648, 1080):
        for y in range(1080):
            image.putpixel((x, y), ((x + y) % 255, y % 255, x % 255))
    output = io.BytesIO()
    image.save(output, format="JPEG", quality=95)
    return output.getvalue()


def test_premium_finish_keeps_the_protected_product_region() -> None:
    source = _poster()
    before = Image.open(io.BytesIO(source)).convert("RGB")
    enhanced = premium_finish(
        source,
        platform="instagram",
        copy={"brand": "Rexona"},
        art_direction={"accent": "#198FA1", "ink": "#172B4D"},
    )
    after = Image.open(io.BytesIO(enhanced)).convert("RGB")

    assert after.size == (1080, 1080)
    assert after.getpixel((100, 900)) != before.getpixel((100, 900))
    drift = []
    for x in range(650, 1080, 67):
        for y in range(0, 1080, 71):
            drift.append(
                sum(abs(a - b) for a, b in zip(before.getpixel((x, y)), after.getpixel((x, y)))) / 3
            )
    assert sum(drift) / len(drift) < 8
