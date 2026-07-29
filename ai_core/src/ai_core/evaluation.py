from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import sys
import warnings
from collections import Counter
from pathlib import Path
from typing import Annotated, Any, Literal

from PIL import Image, ImageChops, ImageFilter, ImageOps, UnidentifiedImageError
from pydantic import (
    Field,
    StrictBool,
    StringConstraints,
    ValidationError,
    field_validator,
    model_validator,
)

from .artifacts import validate_bundle
from .schemas import (
    CandidateBox,
    NormalizedBox,
    Sha256,
    StrictModel,
)


MAX_EVALUATION_PIXELS = 40_000_000
AUTOMATIC_BOX_IOU_THRESHOLD = 0.50
TARGET_AUTOMATIC_SELECTION = 0.90
TARGET_RECOVERABILITY = 1.0
TARGET_MASK_IOU = 0.90
TARGET_BOUNDARY_F1 = 0.85
TARGET_COLD_SECONDS = 15 * 60
TARGET_WARM_SECONDS = 5 * 60

RecordId = Annotated[
    str,
    StringConstraints(
        strict=True,
        strip_whitespace=True,
        min_length=1,
        max_length=100,
        pattern=r"^[A-Za-z0-9_.-]+$",
    ),
]
RelativePath = Annotated[
    str,
    StringConstraints(
        strict=True,
        strip_whitespace=True,
        min_length=1,
        max_length=500,
    ),
]
AuthorizationReceipt = Annotated[
    str,
    StringConstraints(
        strict=True,
        pattern=r"^sha256:[0-9a-f]{64}$",
    ),
]


class EvaluationMetadata(StrictModel):
    id: RecordId
    difficulty: Literal["standard", "difficult"]
    image: RelativePath
    mime: Literal["image/jpeg", "image/png", "image/webp"]
    sha256: Sha256
    width: int = Field(strict=True, gt=0, le=20_000)
    height: int = Field(strict=True, gt=0, le=20_000)
    target_box: NormalizedBox
    reviewed_mask: RelativePath | None
    authorized: StrictBool
    authorization_receipt: AuthorizationReceipt
    notes: Annotated[
        str,
        StringConstraints(
            strict=True,
            strip_whitespace=True,
            max_length=500,
        ),
    ] = ""

    @model_validator(mode="after")
    def enforce_authorization_and_pixels(self) -> EvaluationMetadata:
        if not self.authorized:
            raise ValueError("evaluation images require authorization")
        if self.width * self.height > MAX_EVALUATION_PIXELS:
            raise ValueError("evaluation image exceeds the 40M pixel limit")
        return self


class EvaluationRun(StrictModel):
    id: RecordId
    variant: RecordId
    seed: int = Field(strict=True, ge=0, le=4_294_967_295)
    status: Literal["done", "error"]
    automatic_box: NormalizedBox | None = None
    candidates: tuple[CandidateBox, ...] = Field(
        default_factory=tuple,
        max_length=20,
    )
    generated_mask: RelativePath | None = None
    bundle: RelativePath | None = None
    claim_safety_passed: StrictBool
    product_pixels_preserved: StrictBool
    duration_seconds: float = Field(strict=True, gt=0)
    cache_state: Literal["cold", "warm"]

    @field_validator("duration_seconds")
    @classmethod
    def finite_duration(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("duration must be finite")
        return value

    @model_validator(mode="after")
    def done_requires_artifacts(self) -> EvaluationRun:
        if self.status == "done" and (
            self.generated_mask is None or self.bundle is None
        ):
            raise ValueError("done evaluation runs require mask and bundle paths")
        return self


class EvaluationError(RuntimeError):
    pass


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise EvaluationError("evaluation JSON contains a duplicate key")
        result[key] = value
    return result


def _load_jsonl(path: Path, model: type[StrictModel]) -> list[StrictModel]:
    records: list[StrictModel] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise EvaluationError("evaluation JSONL could not be read") from exc
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line, object_pairs_hook=_strict_object)
            records.append(model.model_validate(raw))
        except (json.JSONDecodeError, ValidationError, EvaluationError) as exc:
            raise EvaluationError(
                f"evaluation JSONL record {line_number} is invalid"
            ) from exc
    return records


def _resolve_private(root: Path, raw: str) -> Path:
    relative = Path(raw)
    if relative.is_absolute():
        raise EvaluationError("evaluation artifact path must be relative")
    try:
        resolved = (root / relative).resolve(strict=True)
    except OSError as exc:
        raise EvaluationError("evaluation artifact is missing") from exc
    if not resolved.is_relative_to(root):
        raise EvaluationError("evaluation artifact escapes its private root")
    return resolved


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _load_source_image(path: Path) -> tuple[Image.Image, str]:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path) as probe:
                detected_format = probe.format or ""
                if probe.width * probe.height > MAX_EVALUATION_PIXELS:
                    raise EvaluationError(
                        "evaluation image exceeds the 40M pixel limit"
                    )
                probe.verify()
            with Image.open(path) as source:
                corrected = ImageOps.exif_transpose(source)
                if corrected.width * corrected.height > MAX_EVALUATION_PIXELS:
                    raise EvaluationError(
                        "EXIF-corrected evaluation image exceeds the pixel limit"
                    )
                corrected.load()
                image = corrected.convert("RGB")
    except EvaluationError:
        raise
    except (
        OSError,
        UnidentifiedImageError,
        Image.DecompressionBombWarning,
        Image.DecompressionBombError,
    ) as exc:
        raise EvaluationError("evaluation image cannot be safely decoded") from exc
    mime = {
        "JPEG": "image/jpeg",
        "PNG": "image/png",
        "WEBP": "image/webp",
    }.get(detected_format)
    if mime is None:
        raise EvaluationError("evaluation image format is unsupported")
    return image, mime


def _load_binary_mask(path: Path, expected_size: tuple[int, int]) -> Image.Image:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path) as source:
                if source.format != "PNG" or source.mode != "L":
                    raise EvaluationError(
                        "evaluation mask must be a single-channel PNG"
                    )
                source.load()
                mask = source.copy()
    except EvaluationError:
        raise
    except (
        OSError,
        UnidentifiedImageError,
        Image.DecompressionBombWarning,
        Image.DecompressionBombError,
    ) as exc:
        raise EvaluationError("evaluation mask cannot be safely decoded") from exc
    if mask.size != expected_size:
        raise EvaluationError("evaluation mask dimensions differ from the source")
    histogram = mask.histogram()
    if any(count for value, count in enumerate(histogram) if value not in {0, 255}):
        raise EvaluationError("evaluation mask must be binary")
    if histogram[255] == 0:
        raise EvaluationError("evaluation mask must not be empty")
    return mask


def _box_iou(first: NormalizedBox, second: NormalizedBox) -> float:
    x0 = max(first.x, second.x)
    y0 = max(first.y, second.y)
    x1 = min(first.x + first.width, second.x + second.width)
    y1 = min(first.y + first.height, second.y + second.height)
    intersection = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    union = (
        first.width * first.height
        + second.width * second.height
        - intersection
    )
    return intersection / union if union else 0.0


def _mask_iou(predicted: Image.Image, reviewed: Image.Image) -> float:
    predicted_l = predicted.convert("L")
    reviewed_l = reviewed.convert("L")
    intersection = ImageChops.multiply(predicted_l, reviewed_l).histogram()[255]
    union = ImageChops.lighter(predicted_l, reviewed_l).histogram()[255]
    return intersection / union if union else 1.0


def _boundary(mask: Image.Image) -> Image.Image:
    eroded = mask.filter(ImageFilter.MinFilter(3))
    return ImageChops.difference(mask, eroded)


def _boundary_f1(
    predicted: Image.Image,
    reviewed: Image.Image,
    *,
    tolerance: int = 1,
) -> float:
    predicted_boundary = _boundary(predicted)
    reviewed_boundary = _boundary(reviewed)
    predicted_count = predicted_boundary.histogram()[255]
    reviewed_count = reviewed_boundary.histogram()[255]
    if predicted_count == 0 and reviewed_count == 0:
        return 1.0
    if predicted_count == 0 or reviewed_count == 0:
        return 0.0
    size = tolerance * 2 + 1
    expanded_reviewed = reviewed_boundary.filter(ImageFilter.MaxFilter(size))
    expanded_predicted = predicted_boundary.filter(ImageFilter.MaxFilter(size))
    matched_predicted = ImageChops.multiply(
        predicted_boundary,
        expanded_reviewed,
    ).histogram()[255]
    matched_reviewed = ImageChops.multiply(
        reviewed_boundary,
        expanded_predicted,
    ).histogram()[255]
    precision = matched_predicted / predicted_count
    recall = matched_reviewed / reviewed_count
    return (
        2 * precision * recall / (precision + recall)
        if precision + recall
        else 0.0
    )


def _validate_metadata(
    records: list[EvaluationMetadata],
    dataset_root: Path,
) -> dict[str, Image.Image]:
    if len(records) != 24:
        raise EvaluationError("evaluation metadata must contain exactly 24 records")
    ids = [record.id for record in records]
    if len(ids) != len(set(ids)):
        raise EvaluationError("evaluation record IDs must be unique")
    counts = Counter(record.difficulty for record in records)
    if counts != {"standard": 12, "difficult": 12}:
        raise EvaluationError(
            "evaluation split must contain 12 standard and 12 difficult records"
        )
    reviewed_count = sum(record.reviewed_mask is not None for record in records)
    if reviewed_count < 12:
        raise EvaluationError(
            "evaluation metadata requires at least 12 reviewed masks"
        )

    reviewed_masks: dict[str, Image.Image] = {}
    seen_hashes: set[str] = set()
    for record in records:
        image_path = _resolve_private(dataset_root, record.image)
        if _sha256_file(image_path) != record.sha256:
            raise EvaluationError("evaluation image checksum does not match metadata")
        if record.sha256 in seen_hashes:
            raise EvaluationError("evaluation set contains duplicate source images")
        seen_hashes.add(record.sha256)
        image, mime = _load_source_image(image_path)
        if mime != record.mime or image.size != (record.width, record.height):
            raise EvaluationError(
                "evaluation source MIME or dimensions differ from metadata"
            )
        if record.reviewed_mask is not None:
            reviewed_masks[record.id] = _load_binary_mask(
                _resolve_private(dataset_root, record.reviewed_mask),
                image.size,
            )
    return reviewed_masks


def evaluate(
    *,
    metadata_path: Path,
    runs_path: Path,
    dataset_root: Path,
    artifacts_root: Path,
) -> dict[str, Any]:
    dataset_root = dataset_root.resolve(strict=True)
    artifacts_root = artifacts_root.resolve(strict=True)
    metadata = [
        record
        for record in _load_jsonl(metadata_path, EvaluationMetadata)
        if isinstance(record, EvaluationMetadata)
    ]
    runs = [
        record
        for record in _load_jsonl(runs_path, EvaluationRun)
        if isinstance(record, EvaluationRun)
    ]
    reviewed_masks = _validate_metadata(metadata, dataset_root)
    metadata_by_id = {record.id: record for record in metadata}

    grouped: dict[str, list[EvaluationRun]] = {}
    for run in runs:
        if run.id not in metadata_by_id:
            raise EvaluationError("evaluation run references an unknown record")
        grouped.setdefault(run.variant, []).append(run)
    if not grouped:
        raise EvaluationError("evaluation runs must contain at least one variant")

    variant_summaries: list[dict[str, Any]] = []
    for variant in sorted(grouped):
        variant_runs = grouped[variant]
        run_ids = [run.id for run in variant_runs]
        if len(variant_runs) != 24 or set(run_ids) != set(metadata_by_id):
            raise EvaluationError(
                "each model variant requires exactly one run per evaluation record"
            )
        if len(run_ids) != len(set(run_ids)):
            raise EvaluationError("model variant contains duplicate run records")

        automatic_correct = 0
        automatic_misses = 0
        recoverable_misses = 0
        mask_ious: list[float] = []
        boundary_scores: list[float] = []
        bundles_valid = 0
        claims_valid = 0
        pixels_valid = 0
        completed = 0
        durations: dict[str, list[float]] = {"cold": [], "warm": []}

        for run in variant_runs:
            metadata_record = metadata_by_id[run.id]
            correct = (
                run.automatic_box is not None
                and _box_iou(run.automatic_box, metadata_record.target_box)
                >= AUTOMATIC_BOX_IOU_THRESHOLD
            )
            automatic_correct += int(correct)
            if not correct:
                automatic_misses += 1
                recoverable_misses += int(
                    any(
                        _box_iou(candidate, metadata_record.target_box)
                        >= AUTOMATIC_BOX_IOU_THRESHOLD
                        for candidate in run.candidates
                    )
                )

            if run.status == "done":
                completed += 1
                try:
                    bundle_path = _resolve_private(artifacts_root, run.bundle or "")
                    validate_bundle(bundle_path.read_bytes())
                    bundles_valid += 1
                except Exception:
                    pass
                claims_valid += int(run.claim_safety_passed)
                pixels_valid += int(run.product_pixels_preserved)

            reviewed = reviewed_masks.get(run.id)
            if reviewed is not None:
                if run.generated_mask is None:
                    mask_ious.append(0.0)
                    boundary_scores.append(0.0)
                else:
                    try:
                        predicted = _load_binary_mask(
                            _resolve_private(artifacts_root, run.generated_mask),
                            reviewed.size,
                        )
                        mask_ious.append(_mask_iou(predicted, reviewed))
                        boundary_scores.append(
                            _boundary_f1(predicted, reviewed)
                        )
                    except EvaluationError:
                        mask_ious.append(0.0)
                        boundary_scores.append(0.0)
            durations[run.cache_state].append(run.duration_seconds)

        automatic_rate = automatic_correct / 24
        recoverability = (
            recoverable_misses / automatic_misses
            if automatic_misses
            else 1.0
        )
        median_iou = statistics.median(mask_ious)
        median_boundary = statistics.median(boundary_scores)
        cold_max = max(durations["cold"]) if durations["cold"] else None
        warm_max = max(durations["warm"]) if durations["warm"] else None
        gates = {
            "all_runs_completed": completed == 24,
            "automatic_selection": automatic_rate
            >= TARGET_AUTOMATIC_SELECTION,
            "ambiguity_recoverability": recoverability
            >= TARGET_RECOVERABILITY,
            "median_mask_iou": median_iou >= TARGET_MASK_IOU,
            "median_boundary_f1": median_boundary >= TARGET_BOUNDARY_F1,
            "bundle_contract": bundles_valid == 24,
            "claim_safety": claims_valid == 24,
            "product_pixel_preservation": pixels_valid == 24,
            "cold_timing": cold_max is not None
            and cold_max <= TARGET_COLD_SECONDS,
            "warm_timing": warm_max is not None
            and warm_max <= TARGET_WARM_SECONDS,
        }
        variant_summaries.append(
            {
                "variant": variant,
                "runs": 24,
                "metrics": {
                    "automatic_selection_rate": round(automatic_rate, 6),
                    "ambiguity_recoverability_rate": round(
                        recoverability,
                        6,
                    ),
                    "reviewed_mask_count": len(mask_ious),
                    "median_mask_iou": round(median_iou, 6),
                    "median_boundary_f1": round(median_boundary, 6),
                    "valid_bundles": bundles_valid,
                    "claim_safe_runs": claims_valid,
                    "pixel_preserved_runs": pixels_valid,
                    "cold_max_seconds": cold_max,
                    "warm_max_seconds": warm_max,
                },
                "gates": gates,
                "passes": all(gates.values()),
            }
        )

    comparison = [
        {
            "variant": summary["variant"],
            "passes": summary["passes"],
            "automatic_selection_rate": summary["metrics"][
                "automatic_selection_rate"
            ],
            "median_mask_iou": summary["metrics"]["median_mask_iou"],
            "median_boundary_f1": summary["metrics"]["median_boundary_f1"],
            "cold_max_seconds": summary["metrics"]["cold_max_seconds"],
            "warm_max_seconds": summary["metrics"]["warm_max_seconds"],
        }
        for summary in sorted(
            variant_summaries,
            key=lambda item: (
                not item["passes"],
                -item["metrics"]["automatic_selection_rate"],
                -item["metrics"]["median_mask_iou"],
                item["metrics"]["warm_max_seconds"]
                if item["metrics"]["warm_max_seconds"] is not None
                else math.inf,
            ),
        )
    ]
    return {
        "schema_version": "1.0",
        "dataset": {
            "records": 24,
            "standard": 12,
            "difficult": 12,
            "reviewed_masks": len(reviewed_masks),
        },
        "variants": variant_summaries,
        "comparison": comparison,
        "passes": all(item["passes"] for item in variant_summaries),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate and score the private Cosmetique AI V1 evaluation set "
            "without emitting private record identifiers or paths."
        )
    )
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--runs", type=Path, required=True)
    parser.add_argument("--dataset-root", type=Path, required=True)
    parser.add_argument("--artifacts-root", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = evaluate(
            metadata_path=args.metadata,
            runs_path=args.runs,
            dataset_root=args.dataset_root,
            artifacts_root=args.artifacts_root,
        )
    except (EvaluationError, OSError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "passes": False,
                    "error": str(exc),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2

    serialized = json.dumps(
        report,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    )
    if args.output is None:
        print(serialized)
    else:
        args.output.write_text(serialized + "\n", encoding="utf-8")
    return 0 if report["passes"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
