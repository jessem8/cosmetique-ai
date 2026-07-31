from __future__ import annotations

import re
from collections.abc import Callable
from functools import lru_cache
from importlib import metadata
from pathlib import Path
from typing import Any

from .errors import ModelPolicyError
from .schemas import DependencyRef, ModelRef


PINNED_DEPENDENCIES: tuple[DependencyRef, ...] = (
    DependencyRef(name="pydantic", version="2.9.2"),
    DependencyRef(name="Pillow", version="12.3.0"),
    DependencyRef(name="fastapi", version="0.140.13"),
    DependencyRef(name="starlette", version="1.3.1"),
    DependencyRef(name="httpx", version="0.27.2"),
    DependencyRef(name="uvicorn", version="0.30.6"),
    DependencyRef(name="python-multipart", version="0.0.32"),
    DependencyRef(name="transformers", version="4.57.3"),
    DependencyRef(name="diffusers", version="0.35.2"),
    DependencyRef(name="accelerate", version="1.12.0"),
    DependencyRef(name="bitsandbytes", version="0.49.0"),
    DependencyRef(name="safetensors", version="0.6.2"),
    DependencyRef(name="huggingface-hub", version="0.36.0"),
)

_REQUIRED_MODELS: dict[str, ModelRef] = {
    "grounding_dino": ModelRef(
        repo_id="IDEA-Research/grounding-dino-tiny",
        revision="a2bb814dd30d776dcf7e30523b00659f4f141c71",
        license="apache-2.0",
    ),
    "sam": ModelRef(
        repo_id="facebook/sam-vit-base",
        revision="70c1a07f894ebb5b307fd9eaaee97b9dfc16068f",
        license="apache-2.0",
    ),
    "sdxl": ModelRef(
        repo_id="stabilityai/stable-diffusion-xl-base-1.0",
        revision="462165984030d82259a11f4367a4eed129e94a7b",
        license="openrail++",
    ),
    "qwen": ModelRef(
        repo_id="Qwen/Qwen2.5-7B-Instruct",
        revision="a09a35458c702b33eeacc393d103063234e8bc28",
        license="apache-2.0",
    ),
}

_BIREFNET_METADATA = {
    "repo_id": "ZhengPeng7/BiRefNet",
    "revision": "e2bf8e4460fc8fa32bba5ea4d94b3233d367b0e4",
    "license": "mit",
}

_MODEL_SNAPSHOT_PATTERNS: dict[str, tuple[str, ...]] = {
    "grounding_dino": (
        "config.json",
        "preprocessor_config.json",
        "tokenizer_config.json",
        "tokenizer.json",
        "vocab.txt",
        "special_tokens_map.json",
        "added_tokens.json",
        "*.safetensors",
    ),
    "sam": (
        "config.json",
        "preprocessor_config.json",
        "*.safetensors",
    ),
    "sdxl": (
        "model_index.json",
        "scheduler/*",
        "tokenizer/*",
        "tokenizer_2/*",
        "text_encoder/*.json",
        "text_encoder/*.fp16.safetensors",
        "text_encoder_2/*.json",
        "text_encoder_2/*.fp16.safetensors",
        "unet/*.json",
        "unet/*.fp16.safetensors",
        "vae/*.json",
        "vae/*.fp16.safetensors",
        "feature_extractor/*",
    ),
    "qwen": (
        "config.json",
        "generation_config.json",
        "tokenizer.json",
        "tokenizer_config.json",
        "special_tokens_map.json",
        "merges.txt",
        "vocab.json",
        "*.safetensors",
        "*.safetensors.index.json",
    ),
}

_MODEL_REQUIRED_FILES: dict[str, tuple[tuple[str, ...], ...]] = {
    "grounding_dino": (
        ("config.json",),
        ("preprocessor_config.json",),
        ("tokenizer_config.json",),
        ("vocab.txt", "tokenizer.json"),
        ("model.safetensors", "*.safetensors"),
    ),
    "sam": (
        ("config.json",),
        ("preprocessor_config.json",),
        ("model.safetensors", "*.safetensors"),
    ),
    "sdxl": (
        ("model_index.json",),
        ("scheduler/scheduler_config.json",),
        ("tokenizer/tokenizer_config.json",),
        ("tokenizer_2/tokenizer_config.json",),
        ("text_encoder/config.json",),
        ("text_encoder/*.fp16.safetensors",),
        ("text_encoder_2/config.json",),
        ("text_encoder_2/*.fp16.safetensors",),
        ("unet/config.json",),
        ("unet/*.fp16.safetensors",),
        ("vae/config.json",),
        ("vae/*.fp16.safetensors",),
    ),
    "qwen": (
        ("config.json",),
        ("tokenizer_config.json",),
        ("tokenizer.json", "vocab.json"),
        ("model.safetensors", "model-*.safetensors"),
    ),
}


def pinned_model_refs(
    *,
    enable_birefnet: bool = False,
    custom_code_reviewed: bool = False,
    custom_code_review_evidence: str | None = None,
) -> dict[str, ModelRef]:
    models = dict(_REQUIRED_MODELS)
    if enable_birefnet:
        if (
            not custom_code_reviewed
            or custom_code_review_evidence is None
            or re.fullmatch(
                r"sha256:[0-9a-f]{64}", custom_code_review_evidence
            )
            is None
        ):
            raise ModelPolicyError(
                "BiRefNet custom code requires a completed review and sha256 review evidence"
            )
        models["birefnet"] = ModelRef(
            **_BIREFNET_METADATA,
            custom_code=True,
            custom_code_reviewed=True,
        )
    return models


def _default_snapshot_resolver(
    role: str,
    model: ModelRef,
    *,
    local_files_only: bool,
) -> Path:
    from huggingface_hub import snapshot_download

    return Path(
        snapshot_download(
            repo_id=model.repo_id,
            revision=model.revision,
            allow_patterns=list(_MODEL_SNAPSHOT_PATTERNS[role]),
            local_files_only=local_files_only,
        )
    )


def _snapshot_is_complete(role: str, snapshot: Path) -> bool:
    if not snapshot.is_dir():
        return False
    for alternatives in _MODEL_REQUIRED_FILES[role]:
        if not any(any(snapshot.glob(pattern)) for pattern in alternatives):
            return False
    return True


def prefetch_pinned_model_snapshots(
    resolver: Callable[[str, ModelRef], str | Path] | None = None,
) -> dict[str, str]:
    """
    Fetch every immutable model snapshot before declaring the runtime ready.

    The returned paths are reader diagnostics only. Model adapters subsequently
    use ``local_files_only=True`` so an accepted job never begins a network fetch.
    """

    resolved: dict[str, str] = {}
    for role, model in pinned_model_refs().items():
        try:
            raw_path = (
                resolver(role, model)
                if resolver is not None
                else _default_snapshot_resolver(
                    role,
                    model,
                    local_files_only=False,
                )
            )
            path = Path(raw_path)
        except Exception as exc:
            raise ModelPolicyError(
                f"immutable {role} snapshot could not be fetched"
            ) from exc
        if not _snapshot_is_complete(role, path):
            raise ModelPolicyError(
                f"immutable {role} snapshot is incomplete"
            )
        resolved[role] = str(path)
    _cached_runtime_prerequisite_report.cache_clear()
    return resolved


def _default_local_snapshot_probe(role: str, model: ModelRef) -> Path:
    return _default_snapshot_resolver(
        role,
        model,
        local_files_only=True,
    )


def _default_bitsandbytes_probe(torch: Any) -> bool:
    try:
        import os
        import glob
        target = "/usr/local/cuda/lib64/libnvJitLink.so.13"
        if not os.path.exists(target):
            candidates = (
                glob.glob("/usr/local/cuda*/lib64/libnvJitLink.so*") +
                glob.glob("/usr/lib/x86_64-linux-gnu/libnvJitLink.so*") +
                glob.glob("/usr/local/lib/python*/dist-packages/nvidia/*/lib/libnvJitLink.so*")
            )
            for c in candidates:
                if os.path.exists(c) and c != target:
                    try:
                        os.makedirs(os.path.dirname(target), exist_ok=True)
                        os.symlink(c, target)
                        break
                    except Exception:
                        pass

        import bitsandbytes.cextension as bnb_cextension
        from bitsandbytes.nn import Linear4bit

        library = getattr(bnb_cextension, "lib", None)
        if library is None or not bool(
            getattr(library, "compiled_with_cuda", False)
        ):
            return False
        layer = Linear4bit(
            16,
            16,
            bias=False,
            compute_dtype=torch.float16,
            quant_type="nf4",
        ).to("cuda")
        sample = torch.zeros((1, 16), device="cuda", dtype=torch.float16)
        with torch.inference_mode():
            output = layer(sample)
        compatible = tuple(output.shape) == (1, 16)
        del output, sample, layer
        torch.cuda.empty_cache()
        return compatible
    except Exception:
        return False


def runtime_dependency_refs() -> tuple[DependencyRef, ...]:
    """Report pinned Python packages plus the actual Colab torch/CUDA runtime."""

    try:
        import torch
    except ImportError as exc:
        raise ModelPolicyError("PyTorch is not installed in the GPU runtime") from exc
    return (
        *PINNED_DEPENDENCIES,
        DependencyRef(name="torch", version=str(torch.__version__)),
        DependencyRef(
            name="cuda_runtime",
            version=str(torch.version.cuda or "unavailable"),
        ),
    )


def runtime_prerequisites_ready() -> bool:
    """Fail-closed readiness check with immutable offline snapshot validation."""

    return bool(runtime_prerequisite_report()["ready"])


def _build_runtime_prerequisite_report(
    *,
    snapshot_probe: Callable[[str, ModelRef], str | Path],
    bitsandbytes_probe: Callable[[Any], bool],
    torch_module: Any | None = None,
) -> dict[str, object]:
    """Build reader-safe diagnostics without loading model tensors."""

    package_report: dict[str, dict[str, str | bool | None]] = {}
    ready = True
    for dependency in PINNED_DEPENDENCIES:
        try:
            actual = metadata.version(dependency.name)
        except metadata.PackageNotFoundError:
            actual = None
        matches = actual == dependency.version
        ready = ready and matches
        package_report[dependency.name] = {
            "expected": dependency.version,
            "actual": actual,
            "matches": matches,
        }
    torch_version: str | None = None
    cuda_runtime: str | None = None
    gpu_name: str | None = None
    total_memory = 0
    cuda_available = False
    try:
        if torch_module is None:
            import torch as imported_torch

            torch = imported_torch
        else:
            torch = torch_module

        cuda_available = bool(torch.cuda.is_available())
        torch_version = str(torch.__version__)
        cuda_runtime = str(torch.version.cuda or "unavailable")
        gpu_name = torch.cuda.get_device_name(0) if cuda_available else None
        total_memory = (
            int(torch.cuda.get_device_properties(0).total_memory)
            if cuda_available
            else 0
        )
    except Exception:
        torch = None

    try:
        from .composition import validate_font_asset

        validate_font_asset()
        font_ready = True
    except Exception:
        font_ready = False

    snapshots: dict[str, dict[str, str | bool | None]] = {}
    snapshots_ready = True
    for role, model in pinned_model_refs().items():
        snapshot_path: str | None = None
        try:
            path = Path(snapshot_probe(role, model))
            complete = _snapshot_is_complete(role, path)
            if complete:
                snapshot_path = str(path)
        except Exception:
            complete = False
        snapshots_ready = snapshots_ready and complete
        snapshots[role] = {
            "repo_id": model.repo_id,
            "revision": model.revision,
            "local": complete,
            "path": snapshot_path,
        }

    bnb_ready = bool(
        torch is not None
        and cuda_available
        and bitsandbytes_probe(torch)
    )
    ready = (
        ready
        and cuda_available
        and font_ready
        and snapshots_ready
        and bnb_ready
    )
    return {
        "ready": ready,
        "packages": package_report,
        "torch": {
            "version": torch_version,
            "cuda_runtime": cuda_runtime,
        },
        "gpu": {
            "available": cuda_available,
            "name": gpu_name,
            "vram_bytes": total_memory,
        },
        "font_ready": font_ready,
        "model_snapshots": snapshots,
        "bitsandbytes_cuda_ready": bnb_ready,
    }


@lru_cache(maxsize=1)
def _cached_runtime_prerequisite_report() -> dict[str, object]:
    return _build_runtime_prerequisite_report(
        snapshot_probe=_default_local_snapshot_probe,
        bitsandbytes_probe=_default_bitsandbytes_probe,
    )


def runtime_prerequisite_report(
    *,
    snapshot_probe: Callable[[str, ModelRef], str | Path] | None = None,
    bitsandbytes_probe: Callable[[Any], bool] | None = None,
    torch_module: Any | None = None,
) -> dict[str, object]:
    """
    Report exact packages, CUDA/4-bit compatibility, font integrity, and
    complete immutable model snapshots.

    Injected probes bypass the process cache so CPU tests can exercise every
    readiness branch without network access or a GPU.
    """

    if (
        snapshot_probe is None
        and bitsandbytes_probe is None
        and torch_module is None
    ):
        return _cached_runtime_prerequisite_report()
    return _build_runtime_prerequisite_report(
        snapshot_probe=snapshot_probe or _default_local_snapshot_probe,
        bitsandbytes_probe=bitsandbytes_probe or _default_bitsandbytes_probe,
        torch_module=torch_module,
    )
