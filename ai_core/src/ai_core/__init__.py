"""Compatibility schemas used by the model-free website worker.

The executable AI pipeline lives in the top-level ai/ package and runs in
Colab. This package intentionally exposes only shared database/API schemas.
"""

from .schemas import CandidateBox, PipelineSnapshot, ProductSnapshot

__all__ = ["CandidateBox", "PipelineSnapshot", "ProductSnapshot"]
__version__ = "0.2.0"
