"""CPU-safe contracts and deterministic artifact tooling for Cosmetique AI."""

from .artifacts import build_manifest, create_bundle, validate_bundle
from .claims import EvidenceLedger, assemble_copy_payload, validate_copy_payload
from .composition import compose_campaign_assets, render_campaign_copy
from .orchestrator import PipelineOrchestrator
from .schemas import GenerationRequest, Manifest

__all__ = [
    "EvidenceLedger",
    "GenerationRequest",
    "Manifest",
    "PipelineOrchestrator",
    "assemble_copy_payload",
    "build_manifest",
    "compose_campaign_assets",
    "create_bundle",
    "render_campaign_copy",
    "validate_bundle",
    "validate_copy_payload",
]

__version__ = "0.1.0"
