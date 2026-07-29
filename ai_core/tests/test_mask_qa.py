from __future__ import annotations

import pytest
from PIL import Image, ImageDraw

from ai_core.errors import MaskQualityError
from ai_core.mask_qa import validate_mask
from ai_core.schemas import NormalizedBox


TARGET = NormalizedBox(type="box", x=0.2, y=0.2, width=0.6, height=0.6)


def rectangle_mask(box: tuple[int, int, int, int]) -> Image.Image:
    mask = Image.new("L", (100, 100), 0)
    ImageDraw.Draw(mask).rectangle(box, fill=255)
    return mask


def test_mask_qa_accepts_a_single_opaque_product_inside_target_box() -> None:
    metrics = validate_mask(rectangle_mask((20, 20, 79, 79)), TARGET)

    assert metrics.area_fraction == pytest.approx(0.36)
    assert metrics.inside_fraction == pytest.approx(1.0)
    assert metrics.bbox_iou == pytest.approx(1.0)
    assert metrics.edge_contact_fraction == pytest.approx(0.0)


def test_mask_qa_rejects_empty_mask() -> None:
    with pytest.raises(MaskQualityError, match="empty"):
        validate_mask(Image.new("L", (100, 100), 0), TARGET)


def test_mask_qa_rejects_mask_mostly_outside_selected_target() -> None:
    with pytest.raises(MaskQualityError, match="outside"):
        validate_mask(rectangle_mask((0, 0, 25, 25)), TARGET)


def test_mask_qa_rejects_nearly_full_frame_segmentation() -> None:
    with pytest.raises(MaskQualityError, match="area"):
        validate_mask(rectangle_mask((0, 0, 99, 99)), TARGET)

