from __future__ import annotations

import gc
import json
from collections.abc import Callable
from typing import Any

from PIL import Image
from pydantic import ValidationError

from .errors import BackgroundContentError, ClaimSafetyError, ModelPolicyError
from .model_registry import pinned_model_refs
from .schemas import (
    CandidateBox,
    CopyPlan,
    GenerationRequest,
    NormalizedBox,
    ProductSnapshot,
)


def _strict_json(value: str) -> Any:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key {key!r}")
            result[key] = item
        return result

    return json.loads(value, object_pairs_hook=reject_duplicates)


def _require_cuda(torch: Any) -> None:
    if not torch.cuda.is_available():
        raise ModelPolicyError(
            "CUDA is unavailable; the pinned GPU models were not loaded"
        )


def _release_cuda(torch: Any) -> None:
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        ipc_collect = getattr(torch.cuda, "ipc_collect", None)
        if callable(ipc_collect):
            ipc_collect()


def _batch_to_device(batch: Any, *, device: str, dtype: Any) -> Any:
    """Move a processor batch to device and cast floating tensors to model dtype."""

    moved = batch.to(device)
    for key, value in list(moved.items()):
        if hasattr(value, "is_floating_point") and value.is_floating_point():
            moved[key] = value.to(dtype=dtype)
    return moved


class _LazyAdapter:
    def __init__(self, *, backend_factory: Callable[[], Any] | None = None) -> None:
        self._backend_factory = backend_factory
        self._backend_instance: Any | None = None

    @property
    def loaded(self) -> bool:
        return self._backend_instance is not None

    def _default_backend(self) -> Any:
        raise NotImplementedError

    def _backend(self) -> Any:
        if self._backend_instance is None:
            factory = self._backend_factory or self._default_backend
            self._backend_instance = factory()
        return self._backend_instance

    def unload(self) -> None:
        backend, self._backend_instance = self._backend_instance, None
        if backend is not None and callable(getattr(backend, "close", None)):
            backend.close()


class GroundingDinoDetector(_LazyAdapter):
    def __init__(
        self,
        *,
        backend_factory: Callable[[], Any] | None = None,
        box_threshold: float = 0.30,
        text_threshold: float = 0.25,
    ) -> None:
        super().__init__(backend_factory=backend_factory)
        self._box_threshold = box_threshold
        self._text_threshold = text_threshold

    def _default_backend(self) -> Any:
        return _GroundingDinoBackend(
            box_threshold=self._box_threshold,
            text_threshold=self._text_threshold,
        )

    def detect(
        self, image: Image.Image, prompt: str
    ) -> tuple[CandidateBox, ...]:
        return tuple(self._backend().detect(image, prompt))


class SamSegmenter(_LazyAdapter):
    def _default_backend(self) -> Any:
        return _SamBackend()

    def segment(self, image: Image.Image, target: NormalizedBox) -> Image.Image:
        return self._backend().segment(image, target).convert("L")


class SdxlBackgroundGenerator(_LazyAdapter):
    def _default_backend(self) -> Any:
        return _SdxlBackend()

    def generate(
        self,
        *,
        prompt: str,
        negative_prompt: str,
        seed: int,
    ) -> Image.Image:
        return self._backend().generate(
            prompt=prompt,
            negative_prompt=negative_prompt,
            seed=seed,
        ).convert("RGB")


class QwenEvidenceSelector(_LazyAdapter):
    def _default_backend(self) -> Any:
        return _QwenBackend()

    def generate(
        self,
        *,
        product: ProductSnapshot,
        request: GenerationRequest,
        ledger: Any,
    ) -> CopyPlan:
        result = self._backend().generate(
            product=product,
            request=request,
            ledger=ledger,
        )
        if isinstance(result, CopyPlan):
            return result
        try:
            return CopyPlan.model_validate(result)
        except ValidationError as exc:
            raise ClaimSafetyError(
                "Qwen output is not a strict evidence-selection plan"
            ) from exc


class GroundingDinoBackgroundValidator:
    """
    Fail-closed generated-background QA using the already pinned detector.

    Unknown detector outcomes or any prohibited scene concept are terminal. The
    validator is deliberately separate from source-product detection so SDXL is
    fully unloaded before this small QA model is loaded.
    """

    _PROHIBITED_PROMPT = (
        "cosmetic product. bottle. tube. jar. packaging. package. label. "
        "logo. writing. text. watermark. person. face. hand."
    )

    def __init__(
        self,
        detector: GroundingDinoDetector | None = None,
    ) -> None:
        self._detector = detector or GroundingDinoDetector(
            box_threshold=0.25,
            text_threshold=0.20,
        )

    def validate(self, image: Image.Image) -> bool:
        if image.mode != "RGB" or image.size != (1024, 1024):
            raise BackgroundContentError(
                "generated background has an unexpected image contract"
            )
        try:
            candidates = self._detector.detect(
                image,
                self._PROHIBITED_PROMPT,
            )
        except BackgroundContentError:
            raise
        except Exception as exc:
            raise BackgroundContentError(
                "generated background QA could not establish a clean scene"
            ) from exc
        if candidates:
            raise BackgroundContentError(
                "generated background contains a prohibited product, package, logo, text, or person"
            )
        return True

    def unload(self) -> None:
        self._detector.unload()


class _GroundingDinoBackend:
    def __init__(self, *, box_threshold: float, text_threshold: float) -> None:
        import torch

        _require_cuda(torch)
        try:
            import torchvision  # noqa: F401
        except Exception:
            import sys
            sys.modules["torchvision"] = None

        from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor

        ref = pinned_model_refs()["grounding_dino"]
        self._torch = torch
        self._device = "cuda"
        self._box_threshold = box_threshold
        self._text_threshold = text_threshold
        self._processor = AutoProcessor.from_pretrained(
            ref.repo_id,
            revision=ref.revision,
            trust_remote_code=False,
            local_files_only=True,
        )
        # float32: Grounding DINO's text/vision path is unreliable under mixed
        # precision on current transformers + Colab torch builds.
        self._model = AutoModelForZeroShotObjectDetection.from_pretrained(
            ref.repo_id,
            revision=ref.revision,
            torch_dtype=torch.float32,
            trust_remote_code=False,
            local_files_only=True,
        ).to(self._device)
        self._model.eval()

    def detect(
        self, image: Image.Image, prompt: str
    ) -> tuple[CandidateBox, ...]:
        normalized_prompt = prompt.strip().rstrip(".") + "."
        inputs = self._processor(
            images=image.convert("RGB"),
            text=normalized_prompt,
            return_tensors="pt",
        ).to(self._device)
        with self._torch.inference_mode():
            outputs = self._model(**inputs)
        result = self._processor.post_process_grounded_object_detection(
            outputs,
            inputs.input_ids,
            threshold=self._box_threshold,
            text_threshold=self._text_threshold,
            target_sizes=[(image.height, image.width)],
        )[0]
        candidates: list[CandidateBox] = []
        ranked = sorted(
            zip(result["scores"].tolist(), result["boxes"].tolist()),
            key=lambda item: item[0],
            reverse=True,
        )
        for index, (score, box) in enumerate(ranked[:20], start=1):
            x0, y0, x1, y1 = box
            x0 = min(1.0, max(0.0, x0 / image.width))
            y0 = min(1.0, max(0.0, y0 / image.height))
            x1 = min(1.0, max(0.0, x1 / image.width))
            y1 = min(1.0, max(0.0, y1 / image.height))
            if x1 <= x0 or y1 <= y0:
                continue
            candidates.append(
                CandidateBox(
                    id=f"candidate-{index}",
                    score=float(score),
                    type="box",
                    x=x0,
                    y=y0,
                    width=x1 - x0,
                    height=y1 - y0,
                )
            )
        return tuple(candidates)

    def close(self) -> None:
        model, self._model = self._model, None
        self._processor = None
        model.to("cpu")
        del model
        _release_cuda(self._torch)


class _SamBackend:
    def __init__(self) -> None:
        import torch

        _require_cuda(torch)
        from transformers import SamModel, SamProcessor

        ref = pinned_model_refs()["sam"]
        self._torch = torch
        self._device = "cuda"
        self._processor = SamProcessor.from_pretrained(
            ref.repo_id,
            revision=ref.revision,
            trust_remote_code=False,
            local_files_only=True,
        )
        self._model = SamModel.from_pretrained(
            ref.repo_id,
            revision=ref.revision,
            torch_dtype=torch.float16,
            trust_remote_code=False,
            local_files_only=True,
        ).to(self._device)
        self._model.eval()

    def segment(self, image: Image.Image, target: NormalizedBox) -> Image.Image:
        box = [
            target.x * image.width,
            target.y * image.height,
            (target.x + target.width) * image.width,
            (target.y + target.height) * image.height,
        ]
        inputs = _batch_to_device(
            self._processor(
                images=image.convert("RGB"),
                input_boxes=[[box]],
                return_tensors="pt",
            ),
            device=self._device,
            dtype=next(self._model.parameters()).dtype,
        )
        with self._torch.inference_mode():
            outputs = self._model(**inputs, multimask_output=True)
        masks = self._processor.image_processor.post_process_masks(
            outputs.pred_masks.cpu(),
            inputs["original_sizes"].cpu(),
            inputs["reshaped_input_sizes"].cpu(),
        )[0][0]
        scores = outputs.iou_scores[0, 0].detach().cpu()
        best_index = int(scores.argmax().item())
        mask = masks[best_index].detach().cpu().numpy()
        return Image.fromarray((mask > 0).astype("uint8") * 255, mode="L")

    def close(self) -> None:
        model, self._model = self._model, None
        self._processor = None
        model.to("cpu")
        del model
        _release_cuda(self._torch)


class _SdxlBackend:
    def __init__(self) -> None:
        import torch

        _require_cuda(torch)
        from diffusers import DiffusionPipeline

        ref = pinned_model_refs()["sdxl"]
        self._torch = torch
        self._pipeline = DiffusionPipeline.from_pretrained(
            ref.repo_id,
            revision=ref.revision,
            variant="fp16",
            torch_dtype=torch.float16,
            use_safetensors=True,
            local_files_only=True,
        )
        self._pipeline.enable_model_cpu_offload()
        self._pipeline.set_progress_bar_config(disable=True)

    def generate(
        self,
        *,
        prompt: str,
        negative_prompt: str,
        seed: int,
    ) -> Image.Image:
        generator = self._torch.Generator(device="cuda").manual_seed(seed)
        output = self._pipeline(
            prompt=prompt,
            negative_prompt=negative_prompt,
            width=1024,
            height=1024,
            num_inference_steps=30,
            guidance_scale=6.5,
            generator=generator,
        )
        return output.images[0]

    def close(self) -> None:
        self._pipeline = None
        _release_cuda(self._torch)


class _QwenBackend:
    def __init__(self) -> None:
        import torch

        _require_cuda(torch)
        from transformers import (
            AutoModelForCausalLM,
            AutoTokenizer,
            BitsAndBytesConfig,
        )

        ref = pinned_model_refs()["qwen"]
        self._torch = torch
        self._tokenizer = AutoTokenizer.from_pretrained(
            ref.repo_id,
            revision=ref.revision,
            trust_remote_code=False,
            local_files_only=True,
        )
        quantization = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_use_double_quant=True,
            bnb_4bit_compute_dtype=torch.float16,
        )
        self._model = AutoModelForCausalLM.from_pretrained(
            ref.repo_id,
            revision=ref.revision,
            device_map="auto",
            quantization_config=quantization,
            trust_remote_code=False,
            local_files_only=True,
        )
        self._model.eval()

    def generate(
        self,
        *,
        product: ProductSnapshot,
        request: GenerationRequest,
        ledger: Any,
    ) -> CopyPlan:
        selectable = [
            {"id": item.id, "kind": item.kind.value, "text": item.text}
            for item in ledger.items
            if item.kind.value
            in {"audience", "benefit", "ingredient", "verified_claim"}
        ]
        schema_example = {
            "language": request.language.value,
            "instagram": {"evidence_ids": ["benefit-1"]},
            "facebook": {"evidence_ids": ["benefit-1"]},
            "linkedin": {"evidence_ids": ["benefit-1"]},
        }
        instruction = (
            "You select evidence IDs for a cosmetics campaign. Return one strict JSON "
            "object only: no markdown and no prose. Use zero to three IDs per platform, "
            "only from the supplied list. Never write or translate advertising copy. "
            "Keep the requested language value unchanged.\n"
            f"Product context: {product.name} / {product.category}\n"
            f"Requested language: {request.language.value}\n"
            f"Selectable evidence: {json.dumps(selectable, ensure_ascii=False)}\n"
            f"Exact shape example: {json.dumps(schema_example, ensure_ascii=False)}"
        )
        messages = [
            {
                "role": "system",
                "content": "Follow the strict evidence-selection contract.",
            },
            {"role": "user", "content": instruction},
        ]
        rendered = self._tokenizer.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
        )
        inputs = self._tokenizer(
            [rendered],
            return_tensors="pt",
        ).to(self._model.device)
        with self._torch.inference_mode():
            generated = self._model.generate(
                **inputs,
                max_new_tokens=300,
                do_sample=False,
                pad_token_id=self._tokenizer.eos_token_id,
            )
        generated = generated[:, inputs.input_ids.shape[1] :]
        output = self._tokenizer.batch_decode(
            generated, skip_special_tokens=True
        )[0].strip()
        try:
            return CopyPlan.model_validate(_strict_json(output))
        except (json.JSONDecodeError, ValueError, ValidationError) as exc:
            raise ClaimSafetyError(
                "Qwen did not return a strict evidence-selection plan"
            ) from exc

    def close(self) -> None:
        self._model = None
        self._tokenizer = None
        _release_cuda(self._torch)
