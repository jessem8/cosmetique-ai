from __future__ import annotations

import hashlib
import io
import sys
import warnings
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from PIL import Image, ImageOps, UnidentifiedImageError

from .artifacts import (
    build_manifest,
    canonical_json_bytes,
    create_bundle,
    validate_bundle,
)
from .claims import EvidenceLedger, assemble_copy_payload, validate_copy_payload
from .composition import (
    assert_exported_product_roi_quality,
    compose_campaign_assets,
    render_campaign_copy,
)
from .errors import (
    BackgroundContentError,
    ClaimSafetyError,
    InvalidGenerationRequestError,
    TargetAmbiguousError,
    TargetNotFoundError,
)
from .mask_qa import validate_mask
from .model_registry import pinned_model_refs, runtime_prerequisites_ready
from .schemas import (
    ArtifactName,
    AssetFormat,
    CandidateBox,
    DependencyRef,
    GenerationStage,
    Manifest,
    ModelRef,
    NormalizedBox,
    RemoteJobRequest,
    StageReceipt,
)


StageCallback = Callable[
    [GenerationStage, tuple[GenerationStage, ...]], None
]


@dataclass(frozen=True, slots=True)
class PipelineOutput:
    bundle: bytes
    manifest: Manifest


def verify_source_request(
    job: RemoteJobRequest,
    source_image: bytes,
    *,
    max_source_pixels: int = 40_000_000,
) -> Image.Image:
    if not 1 <= max_source_pixels <= 100_000_000:
        raise ValueError("source pixel limit must be between 1 and 100M")
    if hashlib.sha256(canonical_json_bytes(job.input)).hexdigest() != (
        job.input_snapshot_hash
    ):
        raise InvalidGenerationRequestError(
            "input snapshot hash does not match the canonical input envelope"
        )
    product = job.input.product
    if hashlib.sha256(source_image).hexdigest() != product.original_sha256:
        raise InvalidGenerationRequestError(
            "uploaded source image checksum does not match the immutable snapshot"
        )
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(source_image)) as probe:
                detected_format = probe.format
                header_size = probe.size
                if header_size[0] * header_size[1] > max_source_pixels:
                    raise InvalidGenerationRequestError(
                        "source image exceeds the runtime pixel-area limit"
                    )
                probe.verify()
            with Image.open(io.BytesIO(source_image)) as decoded:
                corrected = ImageOps.exif_transpose(decoded)
                if corrected.width * corrected.height > max_source_pixels:
                    raise InvalidGenerationRequestError(
                        "EXIF-corrected source exceeds the runtime pixel-area limit"
                    )
                corrected.load()
                image = corrected.convert("RGB")
    except InvalidGenerationRequestError:
        raise
    except (
        UnidentifiedImageError,
        OSError,
        Image.DecompressionBombWarning,
        Image.DecompressionBombError,
    ) as exc:
        raise InvalidGenerationRequestError(
            "uploaded source image is not a decodable supported image"
        ) from exc

    mime = {
        "JPEG": "image/jpeg",
        "PNG": "image/png",
        "WEBP": "image/webp",
    }.get(detected_format or "")
    if mime != product.original_mime:
        raise InvalidGenerationRequestError(
            "uploaded source image MIME does not match the immutable snapshot"
        )
    if image.size != (product.original_width, product.original_height):
        raise InvalidGenerationRequestError(
            "EXIF-corrected source dimensions do not match the immutable snapshot"
        )
    return image


def _select_target(
    candidates: tuple[CandidateBox, ...],
    *,
    ambiguity_delta: float,
) -> NormalizedBox:
    eligible = tuple(
        sorted(
            (candidate for candidate in candidates if candidate.score >= 0.30),
            key=lambda candidate: (-candidate.score, candidate.id),
        )
    )
    if not eligible:
        raise TargetNotFoundError()
    if (
        len(eligible) >= 2
        and eligible[0].score - eligible[1].score <= ambiguity_delta
    ):
        raise TargetAmbiguousError(eligible[:20])
    winner = eligible[0]
    return NormalizedBox(
        type="box",
        x=winner.x,
        y=winner.y,
        width=winner.width,
        height=winner.height,
    )


def _encode_image(
    image: Image.Image,
    *,
    format_name: str,
) -> bytes:
    output = io.BytesIO()
    if format_name == "JPEG":
        image.convert("RGB").save(
            output,
            format="JPEG",
            quality=95,
            subsampling=0,
            optimize=False,
            progressive=False,
        )
    elif format_name == "PNG":
        image.save(output, format="PNG", optimize=False, compress_level=9)
    else:
        raise ValueError(f"unsupported deterministic export format {format_name}")
    return output.getvalue()


def _unload_all(*adapters: object) -> None:
    """Attempt every release and never replace an active pipeline exception."""

    active_exception = sys.exception()
    first_cleanup_error: Exception | None = None
    for adapter in adapters:
        try:
            adapter.unload()
        except Exception as exc:
            if first_cleanup_error is None:
                first_cleanup_error = exc
    if first_cleanup_error is not None and active_exception is None:
        raise first_cleanup_error


class PipelineOrchestrator:
    def __init__(
        self,
        *,
        detector: object,
        segmenter: object,
        background_generator: object,
        background_validator: object,
        copy_generator: object,
        runtime_id: str,
        models: dict[str, ModelRef],
        dependencies: tuple[DependencyRef, ...],
        clock: Callable[[], datetime] | None = None,
        ambiguity_delta: float = 0.08,
    ) -> None:
        self._detector = detector
        self._segmenter = segmenter
        self._background_generator = background_generator
        self._background_validator = background_validator
        self._copy_generator = copy_generator
        self._runtime_id = runtime_id
        self._models = dict(models)
        self._dependencies = dependencies
        self._clock = clock or (lambda: datetime.now(UTC))
        self._ambiguity_delta = ambiguity_delta

    def preflight(self) -> bool:
        return (
            self._models == pinned_model_refs()
            and runtime_prerequisites_ready()
        )

    def run(
        self,
        job: RemoteJobRequest,
        source_image: bytes,
        *,
        on_stage_complete: StageCallback | None = None,
    ) -> PipelineOutput:
        completed: list[GenerationStage] = []
        receipts: list[StageReceipt] = []

        def complete(stage: GenerationStage) -> None:
            if stage is not tuple(GenerationStage)[len(completed)]:
                raise RuntimeError("pipeline attempted to complete stages out of order")
            completed.append(stage)
            receipts.append(StageReceipt(stage=stage, completed_at=self._clock()))
            if on_stage_complete is not None:
                on_stage_complete(stage, tuple(completed))

        image = verify_source_request(job, source_image)
        product = job.input.product
        request = job.input.generation

        try:
            if request.target_hint is None:
                candidates = self._detector.detect(
                    image,
                    f"{product.category} cosmetic personal-care product",
                )
                target = _select_target(
                    tuple(candidates),
                    ambiguity_delta=self._ambiguity_delta,
                )
            else:
                target = request.target_hint
            complete(GenerationStage.ANALYSIS)

            mask = self._segmenter.segment(image, target).convert("L")
            if mask.size != image.size:
                raise InvalidGenerationRequestError(
                    "segmenter returned a mask with different source dimensions"
                )
            validate_mask(mask, target)
            cutout = image.convert("RGBA")
            cutout.putalpha(mask)
            complete(GenerationStage.EXTRACTION)
        finally:
            _unload_all(self._detector, self._segmenter)

        creative_direction = request.creative_direction or (
            "pearl-white premium cosmetic studio, restrained graphite details, "
            "deep raspberry accent, editorial light"
        )
        background_prompt = (
            "Empty premium advertising studio background only; no product, no bottle, "
            "no packaging, no logo, no writing, no letters, no people. "
            f"Category mood: {product.category}. Art direction: {creative_direction}."
        )
        negative_prompt = (
            "product, bottle, tube, jar, package, packaging, label, logo, text, letters, "
            "watermark, person, face, hands, duplicate object, clutter, low quality"
        )
        complete(GenerationStage.ART_DIRECTION)

        background = None
        last_background_error: Exception | None = None
        max_attempts = 3
        for attempt in range(max_attempts):
            current_seed = (request.seed + attempt) % 4_294_967_296
            try:
                candidate_bg = self._background_generator.generate(
                    prompt=background_prompt,
                    negative_prompt=negative_prompt,
                    seed=current_seed,
                ).convert("RGB")
                if candidate_bg.size != (1024, 1024):
                    raise InvalidGenerationRequestError(
                        "background generator must return exactly 1024x1024"
                    )
            finally:
                _unload_all(self._background_generator)

            try:
                clean = self._background_validator.validate(candidate_bg)
                if clean is True:
                    background = candidate_bg
                    break
            except BackgroundContentError as exc:
                last_background_error = exc
            except Exception as exc:
                last_background_error = BackgroundContentError(
                    "generated background QA could not establish a clean scene"
                )
            finally:
                _unload_all(self._background_validator)

        if background is None:
            if last_background_error is not None:
                raise last_background_error
            raise BackgroundContentError(
                "generated background QA could not establish a clean scene"
            )
        complete(GenerationStage.BACKGROUND)

        composed = compose_campaign_assets(background, cutout, mask)
        complete(GenerationStage.COMPOSITION)

        ledger = EvidenceLedger.from_generation_request(
            product_name=product.name,
            category=product.category,
            brand=product.brand,
            request=request,
        )
        try:
            plan = self._copy_generator.generate(
                product=product,
                request=request,
                ledger=ledger,
            )
            if plan.language is not request.language:
                raise ClaimSafetyError(
                    "copy selection language differs from the requested campaign language"
                )
            copy = assemble_copy_payload(plan, ledger)
            validate_copy_payload(copy, ledger)
            complete(GenerationStage.COPY)
        finally:
            _unload_all(self._copy_generator)

        rendered = render_campaign_copy(composed, copy)
        final_payloads: dict[AssetFormat, bytes] = {}
        for platform in AssetFormat:
            asset = rendered[platform]
            payload = _encode_image(asset.image, format_name="JPEG")
            assert_exported_product_roi_quality(
                payload,
                asset.placed_cutout,
                asset.placed_mask,
                asset.position,
            )
            final_payloads[platform] = payload
        artifact_payloads = {
            ArtifactName.INSTAGRAM: final_payloads[AssetFormat.INSTAGRAM],
            ArtifactName.FACEBOOK: final_payloads[AssetFormat.FACEBOOK],
            ArtifactName.LINKEDIN: final_payloads[AssetFormat.LINKEDIN],
            ArtifactName.COPY: canonical_json_bytes(copy),
            ArtifactName.CUTOUT: _encode_image(cutout, format_name="PNG"),
            ArtifactName.MASK: _encode_image(mask.convert("L"), format_name="PNG"),
            ArtifactName.BACKGROUND: _encode_image(
                background, format_name="JPEG"
            ),
        }
        complete(GenerationStage.EXPORT)

        # Record the receipt for the manifest, but publish completion only after
        # creation and self-validation of the final bundle.
        if tuple(GenerationStage)[len(completed)] is not GenerationStage.PACKAGING:
            raise RuntimeError("pipeline attempted to package out of order")
        completed.append(GenerationStage.PACKAGING)
        receipts.append(
            StageReceipt(
                stage=GenerationStage.PACKAGING,
                completed_at=self._clock(),
            )
        )
        manifest = build_manifest(
            generation_id=job.generation_id,
            request_id=job.request_id,
            runtime_id=self._runtime_id,
            input_snapshot_hash=job.input_snapshot_hash,
            seed=request.seed,
            language=request.language,
            target_selection=target,
            dependencies=self._dependencies,
            models=self._models,
            stage_receipts=tuple(receipts),
            artifact_payloads=artifact_payloads,
        )
        bundle = create_bundle(artifact_payloads, manifest)
        validated = validate_bundle(bundle)
        if validated != manifest:
            raise RuntimeError("self-validated manifest differs from packaged manifest")
        if on_stage_complete is not None:
            on_stage_complete(
                GenerationStage.PACKAGING,
                tuple(completed),
            )
        return PipelineOutput(bundle=bundle, manifest=manifest)
