from __future__ import annotations

import math

from .schemas import NormalizedBox, PixelBox


def _validate_image_size(image_width: int, image_height: int) -> None:
    if image_width <= 0 or image_height <= 0:
        raise ValueError("image dimensions must be positive")


def pixel_box_to_normalized(
    box: PixelBox, *, image_width: int, image_height: int
) -> NormalizedBox:
    _validate_image_size(image_width, image_height)
    if box.x_max > image_width or box.y_max > image_height:
        raise ValueError("pixel box exceeds image dimensions")
    return NormalizedBox(
        type="box",
        x=box.x_min / image_width,
        y=box.y_min / image_height,
        width=(box.x_max - box.x_min) / image_width,
        height=(box.y_max - box.y_min) / image_height,
    )


def normalized_box_to_pixels(
    box: NormalizedBox, *, image_width: int, image_height: int
) -> PixelBox:
    _validate_image_size(image_width, image_height)
    x_min = max(0, math.floor(round(box.x * image_width, 12)))
    y_min = max(0, math.floor(round(box.y * image_height, 12)))
    x_max = min(
        image_width, math.ceil(round((box.x + box.width) * image_width, 12))
    )
    y_max = min(
        image_height, math.ceil(round((box.y + box.height) * image_height, 12))
    )
    return PixelBox(x_min=x_min, y_min=y_min, x_max=x_max, y_max=y_max)
