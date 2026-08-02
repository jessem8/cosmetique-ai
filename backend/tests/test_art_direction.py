from __future__ import annotations

from PIL import Image

from ai.colab_cosmetic_poster_pipeline import derive_art_direction, deterministic_studio_background


def _cutout(colour: tuple[int, int, int]) -> Image.Image:
    return Image.new("RGBA", (48, 96), (*colour, 255))


def test_product_conditioned_art_direction_is_stable_and_distinct() -> None:
    first = derive_art_direction(
        _cutout((30, 164, 184)),
        {"brand": "Rexona", "product_name": "Shower Fresh", "category": "deodorant"},
        42,
    )
    repeat = derive_art_direction(
        _cutout((30, 164, 184)),
        {"brand": "Rexona", "product_name": "Shower Fresh", "category": "deodorant"},
        42,
    )
    second = derive_art_direction(
        _cutout((112, 173, 46)),
        {"brand": "SVR", "product_name": "Sebiaclear", "category": "deodorant"},
        42,
    )

    assert first == repeat
    assert (first["accent"], first["layout"]) != (second["accent"], second["layout"])
    assert deterministic_studio_background((320, 320), first).tobytes() != deterministic_studio_background((320, 320), second).tobytes()
