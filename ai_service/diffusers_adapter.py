"""Lazy local Diffusers inpainting adapter."""
from __future__ import annotations

from typing import Any, Callable, Mapping

from PIL import Image

from .image import _coerce_mask
from .segmentation import OptionalDependencyError


class DiffusersInpaintingAdapter:
    """Run a local Diffusers inpainting pipeline without importing it eagerly.

    The V2 mask convention is explicit: white (255) pixels are modified by the
    model, black (0) pixels are preserved.  The adapter does not download
    checkpoints; callers inject a loaded pipeline or factory in production.
    """

    name = "diffusers-inpainting"

    def __init__(self, *, pipeline: Any = None, pipeline_factory: Callable[[], Any] | None = None, model_id: str = "stable-diffusion-xl-1.0-inpainting-0.1") -> None:
        self.pipeline = pipeline
        self.pipeline_factory = pipeline_factory
        self.model_id = model_id

    def _get_pipeline(self) -> Any:
        if self.pipeline is not None:
            return self.pipeline
        if self.pipeline_factory is not None:
            self.pipeline = self.pipeline_factory()
            return self.pipeline
        try:
            import diffusers  # type: ignore  # noqa: F401
        except ImportError as exc:
            raise OptionalDependencyError("diffusers is not installed; inject a loaded pipeline for offline tests") from exc
        raise OptionalDependencyError("Diffusers checkpoint must be loaded/injected; V2 never downloads model weights")

    @staticmethod
    def _extract_image(output: Any) -> Image.Image:
        if isinstance(output, Image.Image):
            return output
        images = getattr(output, "images", None)
        if isinstance(images, (list, tuple)) and images and isinstance(images[0], Image.Image):
            return images[0]
        if isinstance(output, Mapping):
            images = output.get("images")
            if isinstance(images, (list, tuple)) and images and isinstance(images[0], Image.Image):
                return images[0]
        raise ValueError("Diffusers pipeline returned no Pillow image")

    def inpaint(
        self,
        image: Image.Image,
        mask: Image.Image,
        prompt: str,
        *,
        negative_prompt: str | None = None,
        seed: int | None = None,
        width: int | None = None,
        height: int | None = None,
        **kwargs: Any,
    ) -> Image.Image:
        source = image.convert("RGB")
        canonical_mask = _coerce_mask(mask, source.size)
        pipeline = self._get_pipeline()
        call: dict[str, Any] = {
            "prompt": prompt,
            "image": source,
            "mask_image": canonical_mask,
            "negative_prompt": negative_prompt,
        }
        if width is not None:
            call["width"] = int(width)
        if height is not None:
            call["height"] = int(height)
        if seed is not None:
            # Torch remains optional and is imported only when a seeded call is
            # requested.  A caller can also inject a generator through kwargs.
            if "generator" not in kwargs:
                try:
                    import torch  # type: ignore

                    generator = torch.Generator(device="cuda" if torch.cuda.is_available() else "cpu").manual_seed(int(seed))
                    call["generator"] = generator
                except ImportError as exc:
                    # Injected fakes do not need a torch Generator; preserving
                    # the scalar seed keeps offline contract tests model-free.
                    if self.pipeline is not None or self.pipeline_factory is not None:
                        call["seed"] = int(seed)
                    else:
                        raise OptionalDependencyError("torch is required for seeded Diffusers calls") from exc
        call.update(kwargs)
        output = pipeline(**call)
        result = self._extract_image(output)
        if result.size != source.size:
            result = result.resize(source.size, Image.Resampling.LANCZOS)
        return result.convert("RGB")

    def generate(self, image: Image.Image, mask: Image.Image, prompt: str, **kwargs: Any) -> Image.Image:
        """Alias matching the provider-layer generation vocabulary."""

        return self.inpaint(image, mask, prompt, **kwargs)


class FakeInpaintingAdapter(DiffusersInpaintingAdapter):
    """Small deterministic adapter useful for orchestration tests."""

    def __init__(self) -> None:
        super().__init__(pipeline=lambda **kwargs: kwargs["image"])

    def inpaint(self, image: Image.Image, mask: Image.Image, prompt: str, **kwargs: Any) -> Image.Image:
        source = image.convert("RGB")
        canonical_mask = _coerce_mask(mask, source.size)
        # Deterministic scene tint only in the white/modify region.
        result = source.copy()
        overlay = Image.new("RGB", source.size, (224, 232, 234))
        result.paste(overlay, mask=canonical_mask)
        return result


LocalDiffusersInpaintingAdapter = DiffusersInpaintingAdapter


__all__ = ["DiffusersInpaintingAdapter", "FakeInpaintingAdapter", "LocalDiffusersInpaintingAdapter"]
