"""Compatibility exports for V2 product-lock processing."""
from .contracts import MaskMetrics, NormalizedBox, NormalizedPoint, ProductLock, ProductLockStatus, RefinementContract
from .image import *

__all__ = [
    "MaskMetrics",
    "NormalizedBox",
    "NormalizedPoint",
    "ProductLock",
    "ProductLockProcessor",
    "ProductLockStatus",
    "RefinementContract",
    "apply_refinement",
    "assert_mask_accepted",
    "canonicalize_image",
    "decode_image",
    "encode_png",
    "mask_metrics",
]
