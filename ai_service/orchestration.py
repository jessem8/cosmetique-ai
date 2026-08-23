"""Scene planning, background generation, restoration, and fail-closed QA."""
from __future__ import annotations

import colorsys
import hashlib
import io
import math
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol, Sequence

from PIL import Image, ImageChops, ImageFilter, ImageOps

from .contracts import (
    ExecutionPlan,
    GenerationSpec,
    MaskMetrics,
    NormalizedBox,
    PixelComparison,
    ProductLock,
    ProductLockStatus,
    ProductTransform,
    QAResult,
    ScenePlan,
    VariantManifest,
    canonical_json,
    sha256_bytes,
)
from .image import assert_mask_accepted, decode_image, encode_png
from .provider import Provider, ProviderError


CATEGORY_SCENES: dict[str, str] = {
    "deodorant": "pale-aqua bathroom shower scene with a distinct wall and counter, realistic condensation, crisp water droplets, folded white towel texture, soft cool daylight, and clean negative space",
    "perfume": "champagne and rose-gold luxury fragrance scene, glossy reflective surface, warm diffused light, restrained premium styling, and clean negative space",
    "makeup": "editorial beauty scene, satin surface, restrained rose-gold reflections, soft studio light, and clean negative space",
    "skincare": "premium spa scene with pale marble, soft white daylight, calm neutral textures, and clean negative space",
    "haircare": "premium clean bathroom with subtle botanical texture, silk-like highlights, fresh daylight, and clean negative space",
    "sunscreen": "sunlit pale stone beside clear turquoise water, warm daylight, realistic summer atmosphere, and clean negative space",
    "nailcare": "minimal elegant nail salon scene, clean blue-white surface, soft daylight, and clean negative space",
    "bodycare": "warm clean body-care studio, natural pale stone, soft comfortable light, and clean negative space",
    "cosmetics": "minimal premium beauty studio, clean neutral surface, soft commercial light, and clean negative space",
}

NEGATIVE_SCENE_PROMPT = (
    "text, letters, logo, watermark, fake package, extra product, duplicate product, vase, fruit, flowers, "
    "unrelated props, person, human, hands, fingers, floating object, generic magenta studio, blank white background, flat gradient"
)


def _normalized_category(value: str) -> str:
    key = " ".join(str(value or "cosmetics").casefold().replace("-", " ").split())
    aliases = {"déodorant": "deodorant", "parfum": "perfume", "maquillage": "makeup", "soin visage": "skincare", "solaire": "sunscreen", "soin corps": "bodycare"}
    key = aliases.get(key, key)
    return key if key in CATEGORY_SCENES else "cosmetics"


def product_visual_profile(lock: ProductLock) -> dict[str, Any]:
    """Derive a bounded product-specific scene profile from accepted pixels."""

    _, mask, cutout = _open_lock_images(lock)
    bbox = mask.getbbox()
    if bbox is None:
        raise ValueError("product lock mask has no foreground")
    left, top, right, bottom = bbox
    crop = cutout.crop(bbox)
    crop.thumbnail((96, 96), Image.Resampling.LANCZOS)
    pixel_source = crop.get_flattened_data() if hasattr(crop, "get_flattened_data") else crop.getdata()
    pixels = [pixel[:3] for pixel in pixel_source if len(pixel) >= 4 and pixel[3] >= 128]
    if not pixels:
        raise ValueError("product cutout has no visible pixels")
    dominant = tuple(round(sum(pixel[channel] for pixel in pixels) / len(pixels)) for channel in range(3))
    red, green, blue = (value / 255 for value in dominant)
    hue, saturation, brightness = colorsys.rgb_to_hsv(red, green, blue)
    if saturation < 0.12:
        color_family = "neutral"
    elif hue < 0.05 or hue >= 0.95:
        color_family = "red"
    elif hue < 0.12:
        color_family = "amber"
    elif hue < 0.20:
        color_family = "yellow"
    elif hue < 0.45:
        color_family = "green"
    elif hue < 0.72:
        color_family = "blue"
    elif hue < 0.88:
        color_family = "violet"
    else:
        color_family = "rose"
    product_width = max(1, right - left)
    product_height = max(1, bottom - top)
    ratio = product_width / product_height
    silhouette = "tall" if ratio < 0.72 else "wide" if ratio > 1.35 else "balanced"
    profile = {
        "dominant_hex": "#" + "".join(f"{value:02x}" for value in dominant),
        "color_family": color_family,
        "brightness": round(brightness, 3),
        "saturation": round(saturation, 3),
        "silhouette": silhouette,
        "aspect_ratio": round(ratio, 3),
        "coverage_fraction": round(lock.metrics.coverage_fraction, 4),
        "source_sha256": lock.source_sha256,
        "mask_sha256": lock.mask_sha256,
    }
    profile["product_profile_sha256"] = sha256_bytes(canonical_json(profile).encode("utf-8"))
    return profile


PRODUCT_PROFILE_DIRECTIONS = {
    "neutral": "soft mineral neutrals, sculpted shadows, refined material contrast",
    "red": "warm editorial accents, controlled dramatic light, premium lacquer details",
    "amber": "warm stone and golden-hour reflections, restrained luxury materials",
    "yellow": "sunlit cream and pale-stone materials, optimistic natural daylight",
    "green": "botanical mineral textures, fresh diffused daylight, subtle natural depth",
    "blue": "cool mineral surfaces, pale aqua depth, crisp clean daylight",
    "violet": "muted plum and graphite accents, sophisticated directional light",
    "rose": "soft blush stone and satin reflections, polished beauty-editorial lighting",
}


def _provider_canvas_size(width: int, height: int, provider: Provider) -> tuple[int, int]:
    """Use catalog-safe image sizes for remote image-edit APIs.

    The deterministic CPU provider keeps the source dimensions for fast
    contract tests.  CloseRouter/OpenAI image models use the documented 1K
    portrait/landscape/square buckets instead of arbitrary source pixels.
    """

    if getattr(provider, "provider_name", "") != "closerouter":
        return width, height
    ratio = width / height if height else 1.0
    if ratio > 1.2:
        return 1536, 1024
    if ratio < 0.8:
        return 1024, 1536
    return 1024, 1024


class ScenePlanner:
    """Structured, model-agnostic scene planning."""

    def plan(
        self,
        category: str,
        *,
        width: int = 1024,
        height: int = 1024,
        seed: int | None = None,
        creative_direction: str | None = None,
        product_profile: Mapping[str, Any] | None = None,
        provider_capabilities: Any = None,
        model: str | None = None,
    ) -> ScenePlan:
        normalized = _normalized_category(category)
        style = CATEGORY_SCENES[normalized]
        profile = dict(product_profile or {})
        profile_direction = PRODUCT_PROFILE_DIRECTIONS.get(str(profile.get("color_family", "neutral")), PRODUCT_PROFILE_DIRECTIONS["neutral"])
        product_clause = (
            " Product-conditioned art direction: "
            f"dominant palette {profile.get('dominant_hex', '#dfe5e4')}, "
            f"{profile.get('color_family', 'neutral')} color family, "
            f"{profile.get('silhouette', 'balanced')} product silhouette, {profile_direction}."
        )
        direction = (
            f" User creative direction, subordinate to product preservation: {creative_direction.strip()}."
            if creative_direction and creative_direction.strip()
            else ""
        )
        prompt = (
            f"Photorealistic premium cosmetic advertising environment. {style}.{product_clause}{direction} "
            "Generate only the environment around a protected product; leave the product region suitable for exact local restoration. "
            "Use physically plausible contact shadows and commercial lighting. Do not render advertising copy."
        )
        deterministic = bool(getattr(provider_capabilities, "deterministic_seed", False))
        selected_model = model or getattr(provider_capabilities, "model", None)
        return ScenePlan(
            category=normalized,
            prompt=prompt,
            negative_prompt=NEGATIVE_SCENE_PROMPT,
            width=width,
            height=height,
            seed=seed,
            provider_model=selected_model,
            remote_seed_deterministic=deterministic,
            metadata={
                "source": "product-conditioned-scene-planner-v3",
                "direction_supplied": bool(creative_direction and creative_direction.strip()),
                "product_profile_sha256": profile.get("product_profile_sha256"),
                "dominant_hex": profile.get("dominant_hex"),
                "color_family": profile.get("color_family"),
                "silhouette": profile.get("silhouette"),
            },
        )


def _open_lock_images(lock: ProductLock) -> tuple[Image.Image, Image.Image, Image.Image]:
    source = decode_image(lock.canonical_image_png)
    mask = Image.open(io.BytesIO(lock.mask_png)).convert("L")
    cutout = Image.open(io.BytesIO(lock.cutout_png)).convert("RGBA")
    return source, mask, cutout


def _provider_edit_mask(lock: ProductLock) -> bytes:
    """Return an RGBA edit mask that keeps the product opaque.

    CloseRouter/OpenAI-style edits treat transparent pixels as editable.  The
    product region is therefore opaque while the surrounding scene is
    transparent.  The original product is still restored locally after the
    remote edit, so this mask is a provider hint rather than an identity gate.
    """

    mask = Image.open(io.BytesIO(lock.mask_png)).convert("L")
    rgba = Image.new("RGBA", mask.size, (255, 255, 255, 0))
    rgba.putalpha(mask)
    return encode_png(rgba, mode="RGBA")


def _mask_bbox(mask: Image.Image) -> tuple[int, int, int, int]:
    bbox = mask.getbbox()
    if bbox is None:
        raise ValueError("product lock mask has no foreground")
    return tuple(int(value) for value in bbox)


def record_product_transform(
    lock: ProductLock,
    canvas_size: tuple[int, int],
    *,
    placement: NormalizedBox | Mapping[str, Any] | None = None,
) -> ProductTransform:
    """Record deterministic crop/placement without touching the lock."""

    _, mask, _ = _open_lock_images(lock)
    source_bbox = _mask_bbox(mask)
    canvas_width, canvas_height = canvas_size
    source_width = source_bbox[2] - source_bbox[0]
    source_height = source_bbox[3] - source_bbox[1]
    if placement is None:
        width = source_width
        height = source_height
        x = max(0, (canvas_width - width) // 2)
        y = max(0, canvas_height - height - max(4, canvas_height // 12))
    else:
        box = placement if isinstance(placement, NormalizedBox) else NormalizedBox.from_mapping(placement)
        x = round(box.x * canvas_width)
        y = round(box.y * canvas_height)
        width = max(1, round(box.width * canvas_width))
        height = max(1, round(box.height * canvas_height))
    if x + width > canvas_width:
        x = max(0, canvas_width - width)
    if y + height > canvas_height:
        y = max(0, canvas_height - height)
    return ProductTransform(x=x, y=y, width=width, height=height, source_bbox=source_bbox)


def _resample(name: str) -> Image.Resampling:
    return {"nearest": Image.Resampling.NEAREST, "bilinear": Image.Resampling.BILINEAR, "bicubic": Image.Resampling.BICUBIC, "lanczos": Image.Resampling.LANCZOS}.get(name, Image.Resampling.LANCZOS)


def _transformed_product(lock: ProductLock, transform: ProductTransform, canvas_size: tuple[int, int]) -> tuple[Image.Image, Image.Image]:
    _, mask, cutout = _open_lock_images(lock)
    left, top, right, bottom = transform.source_bbox
    product = cutout.crop((left, top, right, bottom))
    product_mask = mask.crop((left, top, right, bottom))
    target_size = (transform.width, transform.height)
    product = product.resize(target_size, _resample(transform.interpolation))
    product_mask = product_mask.resize(target_size, _resample(transform.interpolation)).point(lambda value: 255 if value >= 128 else 0, mode="L")
    layer = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
    layer.paste(product, (transform.x, transform.y), product_mask)
    placed_mask = Image.new("L", canvas_size, 0)
    placed_mask.paste(product_mask, (transform.x, transform.y))
    return layer, placed_mask


def restore_original_pixels(
    background: Image.Image,
    lock: ProductLock,
    *,
    transform: ProductTransform | None = None,
) -> tuple[Image.Image, ProductTransform, Image.Image]:
    """Paste the canonical product back over generated pixels."""

    assert_mask_accepted(lock)
    result = background.convert("RGBA").copy()
    transform = transform or record_product_transform(lock, result.size)
    layer, placed_mask = _transformed_product(lock, transform, result.size)
    result.alpha_composite(layer)
    return result, transform, placed_mask


def restore_canonical_product(
    background: Image.Image,
    lock: ProductLock,
    *,
    transform: ProductTransform | None = None,
) -> Image.Image:
    """Convenience image-only wrapper for callers that do not need receipts."""

    restored, _, _ = restore_original_pixels(background, lock, transform=transform)
    return restored


def compare_interior_pixels(
    actual: Image.Image,
    expected: Image.Image,
    mask: Image.Image,
    *,
    erosion_radius: int = 1,
) -> PixelComparison:
    """Compare exact pixels inside an eroded protected product region."""

    if isinstance(mask, ProductLock):
        mask = Image.open(io.BytesIO(mask.mask_png)).convert("L")
    actual = actual.convert("RGBA")
    expected = expected.convert("RGBA")
    if actual.size != expected.size or actual.size != mask.size:
        raise ValueError("pixel comparison images and mask must have equal dimensions")
    radius = max(0, int(erosion_radius))
    interior = mask.convert("L")
    if radius:
        size = radius * 2 + 1
        interior = interior.filter(ImageFilter.MinFilter(size))
    actual_rgba = actual.load()
    expected_rgba = expected.load()
    interior_pixels = interior.load()
    compared = 0
    differing = 0
    maximum = 0
    before = bytearray()
    after = bytearray()
    for y in range(actual.height):
        for x in range(actual.width):
            if interior_pixels[x, y] == 0:
                continue
            compared += 1
            one = actual_rgba[x, y]
            two = expected_rgba[x, y]
            before.extend(bytes(one))
            after.extend(bytes(two))
            delta = max(abs(int(first) - int(second)) for first, second in zip(one, two))
            maximum = max(maximum, delta)
            differing += int(one != two)
    return PixelComparison(
        compared_pixels=compared,
        differing_pixels=differing,
        exact=differing == 0 and compared > 0,
        max_channel_delta=maximum,
        sha256_before=hashlib.sha256(bytes(before)).hexdigest() if before else None,
        sha256_after=hashlib.sha256(bytes(after)).hexdigest() if after else None,
    )


class DuplicateProductDetector(Protocol):
    def has_duplicate_product(self, image: Image.Image, lock: ProductLock) -> bool | None:
        ...


class PersonHandDetector(Protocol):
    def detect_person_or_hands(self, image: Image.Image) -> Mapping[str, bool] | None:
        ...


DuplicateProductQA = DuplicateProductDetector
PersonHandQA = PersonHandDetector


@dataclass(frozen=True)
class CleanSyntheticQA:
    """Explicitly clean detector for synthetic/offline tests."""

    def has_duplicate_product(self, image: Image.Image, lock: ProductLock) -> bool | None:
        return False

    def detect_person_or_hands(self, image: Image.Image) -> Mapping[str, bool] | None:
        return {"person": False, "hands": False}


def run_fail_closed_qa(
    restored: Image.Image,
    expected: Image.Image,
    protected_mask: Image.Image,
    lock: ProductLock,
    *,
    duplicate_detector: DuplicateProductDetector | None = None,
    person_hand_detector: PersonHandDetector | None = None,
    require_detectors: bool = True,
) -> QAResult:
    comparison = compare_interior_pixels(restored, expected, protected_mask)
    findings: list[str] = []
    if not comparison.exact:
        findings.append("protected_product_pixels_changed")
    duplicate_checked = duplicate_detector is not None
    if duplicate_detector is None:
        if require_detectors:
            findings.append("duplicate_detector_unavailable")
    else:
        duplicate = duplicate_detector.has_duplicate_product(restored, lock)
        if duplicate is None:
            findings.append("duplicate_detector_uncertain")
        elif duplicate:
            findings.append("duplicate_product_detected")
    person_checked = person_hand_detector is not None
    hands_checked = person_hand_detector is not None
    if person_hand_detector is None:
        if require_detectors:
            findings.extend(("person_detector_unavailable", "hands_detector_unavailable"))
    else:
        detections = person_hand_detector.detect_person_or_hands(restored)
        if not isinstance(detections, Mapping):
            findings.append("person_hand_detector_uncertain")
        else:
            if detections.get("person") is not False:
                findings.append("person_detected_or_uncertain")
            if detections.get("hands") is not False:
                findings.append("hands_detected_or_uncertain")
    passed = not findings and lock.status is ProductLockStatus.ACCEPTED
    return QAResult(
        passed=passed,
        status="passed" if passed else "needs_review",
        findings=tuple(findings),
        duplicate_checked=duplicate_checked,
        person_checked=person_checked,
        hands_checked=hands_checked,
        pixel_comparison=comparison,
    )


class FailClosedQAGate:
    """Callable QA facade for dependency-injected workers."""

    def __init__(self, *, duplicate_detector: DuplicateProductDetector | None = None, person_hand_detector: PersonHandDetector | None = None, require_detectors: bool = True) -> None:
        self.duplicate_detector = duplicate_detector
        self.person_hand_detector = person_hand_detector
        self.require_detectors = require_detectors

    def evaluate(self, restored: Image.Image, expected: Image.Image, protected_mask: Image.Image, lock: ProductLock) -> QAResult:
        return run_fail_closed_qa(
            restored,
            expected,
            protected_mask,
            lock,
            duplicate_detector=self.duplicate_detector,
            person_hand_detector=self.person_hand_detector,
            require_detectors=self.require_detectors,
        )


@dataclass(frozen=True)
class GeneratedVariant:
    image: Image.Image
    manifest: VariantManifest
    transform: ProductTransform
    qa: QAResult


@dataclass(frozen=True)
class GenerationOutcome:
    variants: tuple[GeneratedVariant, ...]
    scene_plan: ScenePlan


class BackgroundGenerationOrchestrator:
    """Run a provider plan, restore the product locally, then fail closed QA."""

    def __init__(self, provider: Provider, *, planner: ScenePlanner | None = None) -> None:
        self.provider = provider
        self.planner = planner or ScenePlanner()

    def generate(
        self,
        lock: ProductLock,
        *,
        category: str,
        variants: int = 1,
        width: int | None = None,
        height: int | None = None,
        seed: int | None = None,
        creative_direction: str | None = None,
        max_cost_usd: float | None = None,
        placement: NormalizedBox | Mapping[str, Any] | None = None,
        duplicate_detector: DuplicateProductDetector | None = None,
        person_hand_detector: PersonHandDetector | None = None,
        require_detectors: bool = True,
    ) -> GenerationOutcome:
        assert_mask_accepted(lock)
        if variants < 1 or variants > 16:
            raise ValueError("variants must be between 1 and 16")
        source, _, _ = _open_lock_images(lock)
        profile = product_visual_profile(lock)
        output_width = width or source.width
        output_height = height or source.height
        output_width, output_height = _provider_canvas_size(output_width, output_height, self.provider)
        # Capabilities are discovered by plan() and are not assumed to be
        # deterministic.  The scene plan is updated per variant below only in
        # its seed/capability metadata; no remote seed claim is synthesized.
        first_plan = self.planner.plan(
            category,
            width=output_width,
            height=output_height,
            seed=seed,
            creative_direction=creative_direction,
            product_profile=profile,
        )
        outputs: list[GeneratedVariant] = []
        for index in range(variants):
            variant_seed = None if seed is None else (int(seed) + index) & 0xFFFFFFFF
            spec = GenerationSpec(
                prompt=first_plan.prompt,
                negative_prompt=first_plan.negative_prompt,
                width=output_width,
                height=output_height,
                seed=variant_seed,
                image_bytes=lock.canonical_image_png,
                image_mime="image/png",
                mask_bytes=_provider_edit_mask(lock),
                metadata={
                    "variant_index": index,
                    "lock_id": lock.lock_id,
                    "quality": "low",
                    "input_fidelity": "high",
                    "output_format": "png",
                    "resolution": "1k",
                },
            )
            # plan() performs /credits preflight for remote providers.  This
            # call is intentionally the only route to execute; no paid fallback
            # is attempted after an error or billable request.
            plan = self.provider.plan(spec, operation="edit", max_cost_usd=max_cost_usd)
            result = self.provider.execute(plan)
            generated = decode_image(result.image_bytes).convert("RGBA")
            transform = record_product_transform(lock, generated.size, placement=placement)
            restored, transform, protected_mask = restore_original_pixels(generated, lock, transform=transform)
            expected_layer, expected_mask = _transformed_product(lock, transform, generated.size)
            expected = generated.convert("RGBA").copy()
            expected.alpha_composite(expected_layer)
            qa = run_fail_closed_qa(
                restored,
                expected,
                expected_mask,
                lock,
                duplicate_detector=duplicate_detector,
                person_hand_detector=person_hand_detector,
                require_detectors=require_detectors,
            )
            prompt_hash = hashlib.sha256(first_plan.prompt.encode("utf-8")).hexdigest()
            image_bytes = encode_png(restored, mode="RGBA")
            manifest = VariantManifest(
                variant_id=f"{lock.lock_id}-v{index + 1}",
                request_id=plan.request_id,
                lock_id=lock.lock_id,
                provider=plan.provider,
                model=plan.capabilities.model,
                seed=variant_seed,
                remote_seed_deterministic=plan.capabilities.deterministic_seed,
                prompt_sha256=prompt_hash,
                image_sha256=sha256_bytes(image_bytes),
                pixel_comparison=qa.pixel_comparison or compare_interior_pixels(restored, expected, expected_mask),
                qa=qa,
                cost_usd=result.cost_usd,
                metadata={
                    "transform": transform.to_dict(),
                    "provider_usage": dict(result.usage),
                    "source_sha256": lock.source_sha256,
                    "canonical_image_sha256": lock.canonical_image_sha256,
                    "mask_sha256": lock.mask_sha256,
                    "cutout_sha256": lock.cutout_sha256,
                    "provenance_sha256": lock.provenance_sha256,
                    "scene_plan_sha256": first_plan.plan_sha256,
                    "product_profile_sha256": profile["product_profile_sha256"],
                    "resolved_prompt": first_plan.prompt,
                },
                usage=result.usage,
                attempts=result.attempts,
            )
            outputs.append(GeneratedVariant(restored, manifest, transform, qa))
        capability = outputs[0].manifest if outputs else None
        scene_plan = self.planner.plan(
            category,
            width=output_width,
            height=output_height,
            seed=seed,
            creative_direction=creative_direction,
            product_profile=profile,
            provider_capabilities=getattr(self.provider, "capability", None),
            model=getattr(capability, "model", None) if capability else None,
        )
        return GenerationOutcome(tuple(outputs), scene_plan)


BackgroundGenerator = BackgroundGenerationOrchestrator


__all__ = [
    "BackgroundGenerationOrchestrator",
    "BackgroundGenerator",
    "CATEGORY_SCENES",
    "CleanSyntheticQA",
    "DuplicateProductDetector",
    "DuplicateProductQA",
    "GeneratedVariant",
    "GenerationOutcome",
    "FailClosedQAGate",
    "NEGATIVE_SCENE_PROMPT",
    "PersonHandDetector",
    "PersonHandQA",
    "PRODUCT_PROFILE_DIRECTIONS",
    "ScenePlanner",
    "product_visual_profile",
    "compare_interior_pixels",
    "record_product_transform",
    "restore_original_pixels",
    "restore_canonical_product",
    "run_fail_closed_qa",
]
