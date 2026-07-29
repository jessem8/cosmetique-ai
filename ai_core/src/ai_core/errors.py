from __future__ import annotations

from .schemas import CandidateBox, ErrorCode


class AICoreError(RuntimeError):
    """Base error carrying a stable cross-service error code."""

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


class ClaimSafetyError(AICoreError):
    def __init__(self, message: str) -> None:
        super().__init__(ErrorCode.CLAIM_SAFETY_FAILED, message)


class ArtifactContractError(AICoreError):
    def __init__(self, message: str) -> None:
        super().__init__(ErrorCode.ARTIFACT_CONTRACT_FAILED, message)


class ProductPixelInvariantError(AICoreError):
    def __init__(self, message: str) -> None:
        super().__init__(ErrorCode.ARTIFACT_CONTRACT_FAILED, message)


class TextLayoutError(AICoreError):
    def __init__(self, message: str) -> None:
        super().__init__(ErrorCode.ARTIFACT_CONTRACT_FAILED, message)


class BackgroundContentError(AICoreError):
    def __init__(self, message: str) -> None:
        super().__init__(ErrorCode.ARTIFACT_CONTRACT_FAILED, message)


class ModelPolicyError(AICoreError):
    def __init__(self, message: str) -> None:
        super().__init__(ErrorCode.INVALID_GENERATION_REQUEST, message)


class InvalidGenerationRequestError(AICoreError):
    def __init__(self, message: str) -> None:
        super().__init__(ErrorCode.INVALID_GENERATION_REQUEST, message)


class MaskQualityError(AICoreError):
    def __init__(self, message: str) -> None:
        super().__init__(ErrorCode.MASK_QUALITY_FAILED, message)


class TargetNotFoundError(AICoreError):
    def __init__(self, message: str = "no eligible product target was detected") -> None:
        super().__init__(ErrorCode.TARGET_NOT_FOUND, message)


class TargetAmbiguousError(AICoreError):
    def __init__(self, candidates: tuple[CandidateBox, ...]) -> None:
        super().__init__(
            ErrorCode.TARGET_AMBIGUOUS,
            "multiple eligible product targets were detected",
        )
        self.candidates = candidates
