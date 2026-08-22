"""Compatibility exports for fail-closed generation QA."""
from .orchestration import CleanSyntheticQA, DuplicateProductDetector, DuplicateProductQA, PersonHandDetector, PersonHandQA, compare_interior_pixels, run_fail_closed_qa
from .contracts import PixelComparison, QAResult, VariantManifest

__all__ = ["CleanSyntheticQA", "DuplicateProductDetector", "DuplicateProductQA", "PersonHandDetector", "PersonHandQA", "PixelComparison", "QAResult", "VariantManifest", "compare_interior_pixels", "run_fail_closed_qa"]
