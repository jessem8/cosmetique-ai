from __future__ import annotations

import io
import json
import struct
import zlib
from datetime import UTC, datetime, timedelta

import pytest
from PIL import Image

from ai_core.artifacts import canonical_json_bytes
from ai_core.schemas import (
    ArtifactName,
    CampaignLanguage,
    CopyPayload,
    DependencyRef,
    GenerationStage,
    ModelRef,
    PlatformCopy,
    StageReceipt,
)


@pytest.fixture(scope="session")
def oversized_png_bytes() -> bytes:
    width, height = 8_000, 5_001

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return (
            struct.pack(">I", len(data))
            + body
            + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
        )

    compressor = zlib.compressobj(level=9)
    compressed_parts: list[bytes] = []
    row = b"\x00" + bytes(width)
    for _ in range(height):
        part = compressor.compress(row)
        if part:
            compressed_parts.append(part)
    compressed_parts.append(compressor.flush())
    return b"".join(
        (
            b"\x89PNG\r\n\x1a\n",
            chunk(
                b"IHDR",
                struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0),
            ),
            chunk(b"IDAT", b"".join(compressed_parts)),
            chunk(b"IEND", b""),
        )
    )


def image_bytes(
    mode: str,
    size: tuple[int, int],
    color: int | tuple[int, ...],
    format_name: str,
) -> bytes:
    stream = io.BytesIO()
    Image.new(mode, size, color).save(stream, format=format_name)
    return stream.getvalue()


@pytest.fixture
def copy_payload() -> CopyPayload:
    text = "Sérum Éclat. Hydrate la peau. Découvrir."
    platform = PlatformCopy(text=text, hashtags=("#serum",), claims=())
    return CopyPayload(
        language=CampaignLanguage.FR,
        instagram=platform,
        facebook=platform,
        linkedin=platform,
    )


@pytest.fixture
def artifact_payloads(copy_payload: CopyPayload) -> dict[ArtifactName, bytes]:
    return {
        ArtifactName.INSTAGRAM: image_bytes("RGB", (1080, 1080), (245, 245, 245), "JPEG"),
        ArtifactName.FACEBOOK: image_bytes("RGB", (1200, 630), (244, 244, 244), "JPEG"),
        ArtifactName.LINKEDIN: image_bytes("RGB", (1200, 627), (243, 243, 243), "JPEG"),
        ArtifactName.COPY: canonical_json_bytes(copy_payload),
        ArtifactName.CUTOUT: image_bytes("RGBA", (40, 60), (31, 47, 63, 255), "PNG"),
        ArtifactName.MASK: image_bytes("L", (40, 60), 255, "PNG"),
        ArtifactName.BACKGROUND: image_bytes("RGB", (1024, 1024), (240, 235, 230), "JPEG"),
    }


@pytest.fixture
def pinned_models() -> dict[str, ModelRef]:
    return {
        "grounding_dino": ModelRef(
            repo_id="IDEA-Research/grounding-dino-tiny",
            revision="a" * 40,
            license="apache-2.0",
        ),
        "sam": ModelRef(
            repo_id="facebook/sam-vit-base",
            revision="b" * 40,
            license="apache-2.0",
        ),
        "sdxl": ModelRef(
            repo_id="stabilityai/stable-diffusion-xl-base-1.0",
            revision="c" * 40,
            license="openrail++",
        ),
        "qwen": ModelRef(
            repo_id="Qwen/Qwen2.5-7B-Instruct",
            revision="d" * 40,
            license="apache-2.0",
        ),
    }


@pytest.fixture
def pinned_dependencies() -> tuple[DependencyRef, ...]:
    return (
        DependencyRef(name="pydantic", version="2.9.2"),
        DependencyRef(name="Pillow", version="10.4.0"),
    )


@pytest.fixture
def stage_receipts() -> tuple[StageReceipt, ...]:
    started = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
    return tuple(
        StageReceipt(stage=stage, completed_at=started + timedelta(seconds=index))
        for index, stage in enumerate(GenerationStage)
    )
