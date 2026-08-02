# %% [markdown]
# Colab-first cosmetic campaign pipeline
#
# Runtime requirements:
# - GPU runtime in Google Colab
# - SDXL inpainting checkpoint
# - rembg isnet-general-use + alpha matting
# - PaddleOCR French/Latin label OCR
# - Ollama with Qwen2.5 for strict JSON copy
# - Pillow for deterministic copy rendering
#
# The generated model image never renders advertising text. Text is drawn by Pillow.

from __future__ import annotations

import hmac
import io
import json
import math
import os
import re
import socket
import threading
import time
import urllib.request
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Any, Mapping

from PIL import Image, ImageDraw, ImageOps

if __package__:
    from .campaign_core import (
        FORMATS,
        build_campaign_zip,
        build_inpaint_prompt,
        normalize_category,
        normalize_ocr_text,
        render_poster,
        validate_marketing_copy,
    )
else:
    from campaign_core import (
        FORMATS,
        build_campaign_zip,
        build_inpaint_prompt,
        normalize_category,
        normalize_ocr_text,
        render_poster,
        validate_marketing_copy,
    )


def normalize_ollama_host(value: str | None) -> str:
    """Return a valid Ollama client URL, never a server bind address."""
    host = (value or "").strip().rstrip("/") or "http://127.0.0.1:11434"
    if not re.match(r"^https?://", host, flags=re.IGNORECASE):
        host = f"http://{host}"
    host = re.sub(
        r"^(https?://)(?:0\.0\.0\.0|localhost|\[::\])(?=[:/]|$)",
        r"\g<1>127.0.0.1",
        host,
        flags=re.IGNORECASE,
    )
    return host


SDXL_INPAINT_MODEL = os.getenv(
    "SDXL_INPAINT_MODEL",
    "diffusers/stable-diffusion-xl-1.0-inpainting-0.1",
)
OLLAMA_HOST = normalize_ollama_host(os.getenv("OLLAMA_HOST"))
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b-instruct-q4_K_M")
OLLAMA_FALLBACK_MODELS = tuple(
    model.strip()
    for model in os.getenv(
        "OLLAMA_FALLBACK_MODELS",
        "qwen3:8b,mistral:7b-instruct",
    ).split(",")
    if model.strip()
)
PIPELINE_VERSION = "1.2.0"
RUNTIME_ID = os.getenv("COLAB_RUNTIME_ID", f"colab-runtime-{os.getpid()}")

CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "sunscreen": ("spf", "sunscreen", "sun screen", "solaire", "anthelios", "uvmune", "daylong"),
    "deodorant": ("deodor", "motion sense", "motionsense", "anti-transpir", "shower fresh"),
    "perfume": ("parfum", "perfume", "eau de parfum", "eau de toilette", "idôle", "idole", "nectar"),
    "makeup": ("palette", "mascara", "lipstick", "gloss", "blush", "highlighter", "eyeshadow"),
    "haircare": ("shampoo", "shampooing", "conditioner", "huile capillaire", "hair oil", "hair"),
    "bodycare": (
        "lait corporel", "body wash", "body butter", "baume corporel", "handcreme",
        "hand cream", "crème mains", "creme mains", "shower gel", "gel douche",
        "body mist", "brume corps", "lip balm", "baume lèvres", "baume levres",
        "wipes", "lingettes", "wax", "cire",
    ),
    "skincare": ("serum", "creme", "crème", "gel moussant", "seb iaclear", "hydra"),
    "nailcare": ("dissolvant", "ongles", "nail"),
}

_COPY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "brand": {"type": "string", "minLength": 1},
        "product_name": {"type": "string", "minLength": 1},
        "category": {"type": "string", "minLength": 1},
        "titre": {"type": "string", "minLength": 1},
        "sous_titre": {"type": "string", "minLength": 1},
        "bullets": {"type": "array", "items": {"type": "string", "minLength": 1}, "maxItems": 3},
        "cta": {"type": "string", "minLength": 1},
        "hashtags": {"type": "array", "items": {"type": "string", "minLength": 1}, "maxItems": 5},
        "_meta": {
            "type": "object",
            "properties": {
                "ocr_text": {"type": "string"},
                "source": {"type": "string"},
            },
            "required": ["ocr_text", "source"],
        },
    },
    "required": [
        "brand",
        "product_name",
        "category",
        "titre",
        "sous_titre",
        "bullets",
        "cta",
        "hashtags",
        "_meta",
    ],
}


class PipelineError(RuntimeError):
    """Raised when a required pipeline stage cannot produce a trustworthy result."""


def normalize_target_box(value: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Validate the backend-compatible normalized product box contract."""
    if value is None:
        return None
    if not isinstance(value, Mapping) or value.get("type") != "box":
        raise ValueError("target_box must be an object with type='box'")
    result: dict[str, Any] = {"type": "box"}
    for key in ("x", "y", "width", "height"):
        try:
            number = float(value[key])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"target_box.{key} must be a finite number") from exc
        if not math.isfinite(number):
            raise ValueError(f"target_box.{key} must be finite")
        result[key] = number
    if result["x"] < 0 or result["y"] < 0:
        raise ValueError("target_box origin must be inside the image")
    if result["width"] <= 0 or result["height"] <= 0:
        raise ValueError("target_box width and height must be positive")
    if result["x"] + result["width"] > 1 or result["y"] + result["height"] > 1:
        raise ValueError("target_box must remain inside normalized image bounds")
    return result


def _crop_target(image: Image.Image, target_box: Mapping[str, Any], padding: float = 0.04) -> Image.Image:
    x = float(target_box["x"])
    y = float(target_box["y"])
    width = float(target_box["width"])
    height = float(target_box["height"])
    left = max(0, round((x - padding) * image.width))
    top = max(0, round((y - padding) * image.height))
    right = min(image.width, round((x + width + padding) * image.width))
    bottom = min(image.height, round((y + height + padding) * image.height))
    return image.crop((left, top, right, bottom))


def preprocess_image(image_bytes: bytes, max_side: int = 1536) -> Image.Image:
    image = Image.open(io.BytesIO(image_bytes))
    image = ImageOps.exif_transpose(image).convert("RGB")
    if max(image.size) > max_side:
        scale = max_side / max(image.size)
        image = image.resize(
            (max(1, int(image.width * scale)), max(1, int(image.height * scale))),
            Image.Resampling.LANCZOS,
        )
    return image


def _json_value(value: Any) -> Any:
    if callable(value):
        value = value()
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _find_ocr_payload(value: Any) -> dict[str, Any] | None:
    value = _json_value(value)
    if isinstance(value, dict):
        if "rec_texts" in value:
            return value
        for child in value.values():
            found = _find_ocr_payload(child)
            if found:
                return found
    if isinstance(value, list):
        for child in value:
            found = _find_ocr_payload(child)
            if found:
                return found
    # PaddleOCR 3.x may return a result object whose JSON payload is exposed
    # through a json or res attribute; support both without pinning the API version.
    for attribute in ("json", "res"):
        if hasattr(value, attribute):
            child = getattr(value, attribute)
            if child is not value:
                found = _find_ocr_payload(child)
                if found:
                    return found
    return None


def _parse_ocr_result(result: Any) -> list[dict[str, Any]]:
    payload = _find_ocr_payload(result)
    if not payload:
        return []

    texts = payload.get("rec_texts") or payload.get("text") or []
    scores = payload.get("rec_scores") or payload.get("scores") or []
    if isinstance(texts, str):
        texts = [texts]
    if isinstance(scores, (int, float)):
        scores = [scores]

    items = []
    for index, text in enumerate(texts):
        clean = normalize_ocr_text(str(text))
        if not clean:
            continue
        score = float(scores[index]) if index < len(scores) else 0.0
        items.append({"text": clean, "confidence": round(score, 4)})
    return items


def read_product_label(
    image: Image.Image,
    roi_images: list[Image.Image] | tuple[Image.Image, ...] = (),
) -> dict[str, Any]:
    # PaddleOCR 3.x enables oneDNN by default on CPU. Colab's preloaded
    # PaddlePaddle build can fail in that path, so keep OCR on the stable CPU
    # kernel while SDXL uses the CUDA runtime separately.
    os.environ["FLAGS_use_mkldnn"] = "0"
    os.environ["FLAGS_use_onednn"] = "0"
    try:
        import paddle
        for flag in ("FLAGS_use_mkldnn", "FLAGS_use_onednn"):
            try:
                paddle.set_flags({flag: False})
            except Exception:
                pass
        import numpy as np
        from paddleocr import PaddleOCR
    except ImportError as exc:
        raise PipelineError(
            "PaddleOCR is required in Colab. Install paddleocr and paddlepaddle first."
        ) from exc

    # French is a Latin-language model and covers the English/French labels in the dataset.
    engine = PaddleOCR(
        lang="fr",
        use_doc_orientation_classify=True,
        use_doc_unwarping=False,
        use_textline_orientation=True,
        text_rec_score_thresh=0.25,
        enable_mkldnn=False,
    )
    items: list[dict[str, Any]] = []
    inputs = [image, *roi_images]
    for source_index, source in enumerate(inputs):
        scale = max(1.0, min(3.0, 1280 / max(source.width, source.height)))
        enlarged = source.resize(
            (max(1, round(source.width * scale)), max(1, round(source.height * scale))),
            Image.Resampling.LANCZOS,
        )
        array = np.asarray(enlarged)
        if hasattr(engine, "predict"):
            result = engine.predict(array)
        elif hasattr(engine, "ocr"):
            result = engine.ocr(array, cls=True)
        else:
            raise PipelineError("Unsupported PaddleOCR API: no predict or ocr method available")
        for item in result if isinstance(result, list) else [result]:
            parsed = _parse_ocr_result(item)
            for candidate in parsed:
                candidate["source"] = "full" if source_index == 0 else f"roi_{source_index}"
            items.extend(parsed)

    # Keep the strongest reading for duplicates across the full frame and ROI passes.
    merged: dict[str, dict[str, Any]] = {}
    for item in items:
        key = re.sub(r"\W+", "", item["text"].casefold())
        if key and (key not in merged or item["confidence"] > merged[key]["confidence"]):
            merged[key] = item
    # Preserve first-pass reading order for brand/product inference while
    # replacing duplicate readings with the highest-confidence evidence.
    items = list(merged.values())

    if not items:
        raise PipelineError("PaddleOCR found no readable product label text")

    text = normalize_ocr_text(" ".join(item["text"] for item in items))
    return {"text": text, "items": items, "passes": len(inputs)}


def infer_category_from_text(text: str) -> str:
    normalized = text.casefold()
    for category, keywords in CATEGORY_KEYWORDS.items():
        if any(keyword in normalized for keyword in keywords):
            return category
    return "cosmetics"


def infer_product_metadata(
    ocr: Mapping[str, Any],
    overrides: Mapping[str, str] | None = None,
) -> dict[str, str]:
    overrides = {key: str(value).strip() for key, value in (overrides or {}).items() if value}
    items = [
        item["text"]
        for item in ocr.get("items", [])
        if float(item.get("confidence", 0.0)) >= 0.25 and item.get("text")
    ]
    if not items:
        items = [part for part in normalize_ocr_text(str(ocr.get("text", ""))).split(" ") if part]

    brand = overrides.get("brand") or (items[0] if items else "")
    product_name = overrides.get("product_name") or " ".join(items[1:4])
    category = normalize_category(
        overrides.get("category") or infer_category_from_text(str(ocr.get("text", "")))
    )

    if not brand or not product_name:
        raise PipelineError(
            "OCR did not identify a brand and product name. Provide them as optional metadata."
        )

    return {
        "brand": brand,
        "product_name": product_name,
        "category": category,
        "tone": overrides.get("tone", "premium"),
    }


_RMBG_SESSION: Any = None


def remove_product_background(image: Image.Image) -> tuple[Image.Image, Image.Image]:
    global _RMBG_SESSION
    try:
        from rembg import new_session, remove
    except ImportError as exc:
        raise PipelineError(
            "rembg is required in Colab. Install rembg and onnxruntime-gpu first."
        ) from exc

    if _RMBG_SESSION is None:
        # The Colab runtime can expose CUDA/TensorRT providers which work in a
        # preflight but fail as soon as rembg performs its first inference.
        # Isolation is deliberately CPU-pinned: it is small relative to the
        # original SDXL workload, deterministic, and avoids competing with
        # Paddle/torch for the CUDA runtime.
        _RMBG_SESSION = new_session(
            "isnet-general-use", providers=["CPUExecutionProvider"]
        )

    rgba = image.convert("RGBA")
    try:
        cutout = remove(
            rgba,
            session=_RMBG_SESSION,
            alpha_matting=True,
            alpha_matting_foreground_threshold=240,
            alpha_matting_background_threshold=10,
            alpha_matting_erode_size=10,
            post_process_mask=True,
        ).convert("RGBA")
    except Exception:
        # Alpha matting is a quality enhancement.  A provider-specific
        # matting failure must not terminate the whole campaign request.
        try:
            cutout = remove(
                rgba,
                session=_RMBG_SESSION,
                alpha_matting=False,
                post_process_mask=True,
            ).convert("RGBA")
        except Exception as exc:
            raise PipelineError("Product isolation failed in the Colab runtime") from exc
    mask = cutout.getchannel("A")
    if mask.getbbox() is None:
        raise PipelineError("rembg returned an empty product mask")
    return cutout, mask


def evaluate_mask_quality(mask: Image.Image, *, target_source: str) -> dict[str, Any]:
    """Reject masks that cannot safely preserve a single complete product."""
    alpha = mask.convert("L")
    bbox = alpha.getbbox()
    if bbox is None:
        raise PipelineError("Product isolation returned an empty mask; provide target_hint")
    left, top, right, bottom = bbox
    histogram = alpha.histogram()
    foreground = sum(histogram[16:])
    area_ratio = foreground / max(1, alpha.width * alpha.height)
    bbox_ratio = ((right - left) * (bottom - top)) / max(1, alpha.width * alpha.height)
    edges = {
        "left": left <= 1,
        "top": top <= 1,
        "right": right >= alpha.width - 1,
        "bottom": bottom >= alpha.height - 1,
    }
    metrics = {
        "area_ratio": round(area_ratio, 4),
        "bbox_ratio": round(bbox_ratio, 4),
        "bbox": [left, top, right, bottom],
        "edge_contacts": [name for name, touched in edges.items() if touched],
        "target_source": target_source,
    }
    too_small = area_ratio < 0.012 or (right - left) < 24 or (bottom - top) < 24
    too_large = area_ratio > 0.88
    opposing_edges = (edges["left"] and edges["right"]) or (edges["top"] and edges["bottom"])
    if too_small or too_large or opposing_edges:
        raise PipelineError(
            "Product isolation looks incomplete or suspicious; provide a normalized target_hint box"
        )
    return metrics


def _mask_roi(image: Image.Image, mask: Image.Image, padding: float = 0.08) -> Image.Image:
    bbox = mask.getbbox()
    if bbox is None:
        return image
    left, top, right, bottom = bbox
    pad_x = round((right - left) * padding)
    pad_y = round((bottom - top) * padding)
    return image.crop((
        max(0, left - pad_x),
        max(0, top - pad_y),
        min(image.width, right + pad_x),
        min(image.height, bottom + pad_y),
    ))


def prepare_inpainting_inputs(
    cutout: Image.Image,
    canvas_size: tuple[int, int] = (1024, 1024),
) -> tuple[Image.Image, Image.Image, Image.Image, tuple[int, int]]:
    canvas = Image.new("RGB", canvas_size, (236, 241, 240))
    alpha = cutout.getchannel("A")
    bbox = alpha.getbbox()
    if bbox is None:
        raise PipelineError("Cannot prepare inpainting inputs from an empty alpha mask")

    trimmed = cutout.crop(bbox)
    is_wide = canvas_size[0] / canvas_size[1] > 1.4
    max_width = int(canvas_size[0] * (0.34 if is_wide else 0.40))
    max_height = int(canvas_size[1] * (0.86 if is_wide else 0.82))
    scale = min(max_width / trimmed.width, max_height / trimmed.height)
    product = trimmed.resize(
        (max(1, int(trimmed.width * scale)), max(1, int(trimmed.height * scale))),
        Image.Resampling.LANCZOS,
    )

    position = (
        int(canvas_size[0] * (0.76 if is_wide else 0.74) - product.width / 2),
        int(canvas_size[1] * 0.52 - product.height / 2),
    )
    safe_x = round(canvas_size[0] * 0.05)
    safe_y = round(canvas_size[1] * 0.05)
    position = (
        min(max(safe_x, position[0]), canvas_size[0] - safe_x - product.width),
        min(max(safe_y, position[1]), canvas_size[1] - safe_y - product.height),
    )
    base = canvas.convert("RGBA")
    base.alpha_composite(product, position)

    # Diffusers paints white mask pixels and preserves black pixels.
    mask = Image.new("L", canvas_size, 255)
    mask.paste(0, position, product.getchannel("A"))
    return base.convert("RGB"), mask, product, position


_SDXL_PIPELINE: Any = None


def load_sdxl_inpaint_pipeline() -> Any:
    global _SDXL_PIPELINE
    if _SDXL_PIPELINE is not None:
        return _SDXL_PIPELINE

    try:
        import torch
        from diffusers import StableDiffusionXLInpaintPipeline
    except ImportError as exc:
        raise PipelineError(
            "diffusers and torch are required in Colab for SDXL inpainting."
        ) from exc

    if not torch.cuda.is_available():
        raise PipelineError("A CUDA GPU runtime is required for SDXL inpainting")

    _SDXL_PIPELINE = StableDiffusionXLInpaintPipeline.from_pretrained(
        SDXL_INPAINT_MODEL,
        torch_dtype=torch.float16,
        variant="fp16",
        use_safetensors=True,
    )
    if hasattr(_SDXL_PIPELINE, "enable_model_cpu_offload"):
        _SDXL_PIPELINE.enable_model_cpu_offload()
    else:
        _SDXL_PIPELINE.to("cuda")
    return _SDXL_PIPELINE


def generate_environment(
    base: Image.Image,
    mask: Image.Image,
    category: str,
    seed: int = 42,
) -> Image.Image:
    try:
        import torch
    except ImportError as exc:
        raise PipelineError("torch is required for SDXL inpainting") from exc

    pipeline = load_sdxl_inpaint_pipeline()
    prompt = build_inpaint_prompt(category)
    negative_prompt = (
        "text, letters, typography, logo, watermark, fake packaging, extra bottles, "
        "extra products, vase, fruit, flowers, people, hands, floating product, "
        "magenta studio, unrelated props, distorted object, warped package, "
        "blank white background, empty white studio, featureless backdrop, "
        "white cyclorama, flat gradient, flat color, washed-out environment"
    )
    generator = torch.Generator(device="cuda").manual_seed(seed)
    result = pipeline(
        prompt=prompt,
        negative_prompt=negative_prompt,
        image=base,
        mask_image=mask,
        strength=0.92,
        guidance_scale=7.0,
        num_inference_steps=30,
        generator=generator,
        width=base.width,
        height=base.height,
    ).images[0]
    return result.convert("RGB")


def deterministic_studio_background(
    canvas_size: tuple[int, int], category: str
) -> Image.Image:
    """Build a text-free editorial backdrop without a generative model.

    The final product cutout and all campaign typography are composited later
    with Pillow.  This deliberately avoids SDXL inventing letters, logos, or
    unrelated objects in a paid campaign asset.
    """
    width, height = canvas_size
    palette = {
        "deodorant": ((241, 248, 247), (205, 232, 231), (128, 191, 193)),
        "perfume": ((248, 243, 239), (232, 214, 203), (183, 142, 126)),
        "makeup": ((252, 244, 246), (242, 211, 221), (202, 131, 153)),
    }
    base, accent, line = palette.get(
        normalize_category(category), ((246, 247, 245), (218, 229, 226), (143, 178, 174))
    )
    canvas = Image.new("RGB", canvas_size, base)
    draw = ImageDraw.Draw(canvas, "RGBA")
    # Keep the copy side calm; a few restrained geometric forms create depth
    # solely on the product side, where no generated text can appear.
    draw.ellipse(
        (int(width * 0.52), int(-height * 0.16), int(width * 1.08), int(height * 0.56)),
        fill=(*accent, 125),
    )
    draw.ellipse(
        (int(width * 0.63), int(height * 0.42), int(width * 1.13), int(height * 1.12)),
        fill=(*accent, 105),
    )
    draw.rounded_rectangle(
        (int(width * 0.58), int(height * 0.10), int(width * 0.96), int(height * 0.90)),
        radius=max(18, int(min(width, height) * 0.06)),
        outline=(*line, 115),
        width=max(2, int(min(width, height) * 0.004)),
    )
    return canvas


def _ollama_content(response: Any) -> str:
    message = getattr(response, "message", None)
    if message is None and isinstance(response, Mapping):
        message = response.get("message")
    if isinstance(message, Mapping):
        return str(message.get("content", ""))
    return str(getattr(message, "content", ""))


def generate_marketing_copy(
    ocr: Mapping[str, Any],
    metadata: Mapping[str, str],
    tone: str = "premium",
    language: str = "fr",
) -> dict[str, Any]:
    try:
        from ollama import Client
    except ImportError as exc:
        raise PipelineError(
            "The ollama Python package is required and Ollama must expose Qwen2.5."
        ) from exc

    ocr_text = normalize_ocr_text(str(ocr.get("text", "")))
    language = (language or "fr").strip().lower()
    language_instruction = (
        "Écris tous les textes créatifs exclusivement en français correct, avec les accents."
        if language.startswith("fr")
        else f"Write every creative field in language code {language}."
    )
    user_prompt = f"""
Produit: {metadata["brand"]} {metadata["product_name"]}
Catégorie: {metadata["category"]}
Ton: {tone}
Langue demandée: {language}
Texte réellement lu sur l'étiquette:
{ocr_text}

Retourne uniquement l'objet JSON demandé.
{language_instruction}
Le champ titre doit être une ligne créative courte.
Le sous_titre et les bullets ne peuvent utiliser que des informations compatibles avec le texte OCR.
N'invente aucune promesse médicale, clinique, dermatologique, aucun résultat chiffré absent de l'étiquette et aucun ingrédient absent.
Tous les champs texte obligatoires doivent être non vides. Le CTA doit être un appel à l'action court; si tu hésites, utilise exactement "Découvrir".
La valeur _meta.source doit être exactement la chaîne "ocr+metadata".
"""
    system_prompt = (
        "Tu es un copywriter cosmétique senior. Réponds uniquement avec un JSON conforme au schéma. "
        "Les textes doivent être courts, élégants et lisibles sur mobile. "
        "Ne produis jamais de marque ou de produit de démonstration. "
        "Ne laisse jamais un champ texte obligatoire vide; le CTA doit être non vide."
    )

    client = Client(host=normalize_ollama_host(OLLAMA_HOST))
    configured_models = tuple(
        dict.fromkeys((OLLAMA_MODEL, *OLLAMA_FALLBACK_MODELS))
    )
    errors: list[str] = []

    for model in configured_models:
        model_prompt = user_prompt
        for attempt in range(2):
            try:
                response = client.chat(
                    model=model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": model_prompt},
                    ],
                    format=_COPY_SCHEMA,
                    stream=False,
                    think=False,
                    keep_alive=0,
                    options={"temperature": 0, "seed": 42, "num_ctx": 4096},
                )
                raw = _ollama_content(response)
                payload = json.loads(raw)
                validation_metadata = dict(metadata)
                validation_metadata["language"] = language
                return validate_marketing_copy(payload, ocr_text, validation_metadata)
            except Exception as exc:
                errors.append(f"{model} attempt {attempt + 1}: {exc}")
                model_prompt += (
                    f"\nLa réponse précédente était invalide ({exc}). "
                    "Réponds à nouveau avec tous les champs obligatoires non vides, sans texte hors JSON. "
                    "Pour cta, utilise exactement 'Découvrir' si nécessaire."
                )

    # A malformed or wrong-language model response must never leak into public copy.
    # This fallback uses only caller-verified identity/category fields and makes no claim.
    fallback = _safe_marketing_copy(metadata, ocr_text, language)
    fallback["_meta"]["model_errors"] = errors[-4:]
    return fallback


def _safe_marketing_copy(
    metadata: Mapping[str, str],
    ocr_text: str,
    language: str,
) -> dict[str, Any]:
    category = normalize_category(metadata.get("category"))
    if language.startswith("fr"):
        titles = {
            "deodorant": "Fraîcheur en mouvement",
            "perfume": "Un sillage singulier",
            "makeup": "Votre geste beauté",
            "skincare": "Le soin essentiel",
            "haircare": "L'éclat au quotidien",
            "sunscreen": "Votre rituel solaire",
            "nailcare": "Le geste précis",
            "bodycare": "Un moment pour soi",
            "cosmetics": "Une beauté singulière",
        }
        subtitles = {
            "deodorant": "Un geste frais pour accompagner votre quotidien.",
            "perfume": "Une signature olfactive à découvrir.",
            "makeup": "Une touche expressive, simplement.",
            "skincare": "Un geste simple au cœur de votre rituel.",
            "haircare": "Un rituel pensé pour vos cheveux.",
            "sunscreen": "Un essentiel pour vos moments au soleil.",
            "nailcare": "Une touche soignée jusque dans les détails.",
            "bodycare": "Un geste agréable dans votre rituel.",
            "cosmetics": "Une présence élégante dans votre quotidien.",
        }
        subtitle = subtitles.get(category, subtitles["cosmetics"])
        cta = "Découvrir"
    else:
        titles = {key: "An everyday essential" for key in CATEGORY_KEYWORDS}
        titles["cosmetics"] = "An everyday essential"
        subtitle = "A refined addition to your daily ritual."
        cta = "Discover"
    payload = {
        "brand": str(metadata["brand"]).strip(),
        "product_name": str(metadata["product_name"]).strip(),
        "category": category,
        "titre": titles.get(category, titles["cosmetics"]),
        "sous_titre": subtitle,
        "bullets": [],
        "cta": cta,
        "hashtags": [],
        "_meta": {"ocr_text": normalize_ocr_text(ocr_text), "source": "ocr+metadata", "fallback": True},
    }
    validation_metadata = dict(metadata)
    validation_metadata["language"] = language
    return validate_marketing_copy(payload, ocr_text, validation_metadata)


def _image_from_bytes(value: bytes) -> Image.Image:
    return Image.open(io.BytesIO(value)).convert("RGB")


def generate_campaign_zip(
    image_bytes: bytes,
    *,
    source_name: str = "uploaded_product",
    metadata_overrides: Mapping[str, str] | None = None,
    language: str = "fr",
    target_box: Mapping[str, Any] | None = None,
    seed: int = 42,
    request_context: Mapping[str, Any] | None = None,
) -> bytes:
    started = time.perf_counter()
    timings: dict[str, float] = {}
    stage = time.perf_counter()
    image = preprocess_image(image_bytes)
    timings["preprocess_seconds"] = round(time.perf_counter() - stage, 3)
    normalized_target = normalize_target_box(target_box)
    target_source = "manual" if normalized_target else "automatic"
    isolation_input = _crop_target(image, normalized_target) if normalized_target else image

    stage = time.perf_counter()
    cutout, product_mask = remove_product_background(isolation_input)
    mask_metrics = evaluate_mask_quality(product_mask, target_source=target_source)
    timings["isolation_seconds"] = round(time.perf_counter() - stage, 3)

    stage = time.perf_counter()
    roi = _mask_roi(isolation_input, product_mask)
    roi_inputs = [roi]
    if normalized_target:
        roi_inputs.insert(0, isolation_input)
    ocr = read_product_label(image, roi_images=roi_inputs)
    timings["ocr_seconds"] = round(time.perf_counter() - stage, 3)
    metadata = infer_product_metadata(ocr, metadata_overrides)
    # Public campaign copy stays deterministic and fact-safe.  The optional
    # Ollama helper remains available for preflight experiments, but never
    # controls publishable creative in this fast, reliable render path.
    stage = time.perf_counter()
    copy = _safe_marketing_copy(metadata, str(ocr.get("text", "")), language)
    timings["copy_seconds"] = round(time.perf_counter() - stage, 3)

    stage = time.perf_counter()
    _, _, square_product, square_position = prepare_inpainting_inputs(
        cutout, (1024, 1024)
    )
    square_scene = deterministic_studio_background((1024, 1024), metadata["category"])
    timings["square_scene_seconds"] = round(time.perf_counter() - stage, 3)

    stage = time.perf_counter()
    _, _, wide_product, wide_position = prepare_inpainting_inputs(
        cutout, (1216, 640)
    )
    wide_scene = deterministic_studio_background((1216, 640), metadata["category"])
    timings["wide_scene_seconds"] = round(time.perf_counter() - stage, 3)

    # The renderer receives the original rembg pixels, never the SDXL package.
    posters = {
        "instagram": render_poster(
            square_scene,
            square_product,
            copy,
            "instagram",
            source_product_position=square_position,
        ),
        "facebook": render_poster(
            wide_scene,
            wide_product,
            copy,
            "facebook",
            source_product_position=wide_position,
        ),
        "linkedin": render_poster(
            wide_scene,
            wide_product,
            copy,
            "linkedin",
            source_product_position=wide_position,
        ),
    }
    context = dict(request_context or {})
    timings["total_seconds"] = round(time.perf_counter() - started, 3)
    manifest = {
        "pipeline_version": PIPELINE_VERSION,
        "runtime_id": RUNTIME_ID,
        "generation_id": str(context.get("generation_id", "")),
        "request_id": str(context.get("request_id", "")),
        "input_snapshot_hash": str(context.get("input_snapshot_hash", "")),
        "language": language,
        "copy_language": language,
        "source_name": source_name,
        "category": metadata["category"],
        "scenes": {
            "square": {"dimensions": [1024, 1024], "seed": seed},
            "wide": {"dimensions": [1216, 640], "seed": seed + 1},
        },
        "target_box_source": target_source,
        "target_box": normalized_target,
        "mask_quality": mask_metrics,
        "fonts": {
            "sans": "Manrope Variable",
            "serif": "Cormorant Garamond Variable",
        },
        "timings": timings,
        "models": {
            "background_removal": "isnet-general-use",
            "ocr": "PaddleOCR-lang-fr",
            "background_generation": "deterministic-studio-v1",
            "copy_generation": "deterministic-french-v1",
        },
        "seed": seed,
        "preserves_product_pixels": True,
        "text_rendering": "Pillow",
        "package_versions": _package_versions(),
    }
    diagnostics = {
        "cutout.png": cutout,
        "mask.png": product_mask,
        "background.jpg": square_scene,
    }
    return build_campaign_zip(posters, copy, ocr, manifest, diagnostics=diagnostics)


def _package_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    for package in ("Pillow", "paddleocr", "paddlepaddle", "rembg", "diffusers", "torch", "ollama"):
        try:
            versions[package] = importlib_metadata.version(package)
        except importlib_metadata.PackageNotFoundError:
            versions[package] = "not-installed"
    return versions


def generate_text_only(
    ocr_text: str,
    brand: str,
    product_name: str,
    category: str,
    tone: str = "premium",
    language: str = "fr",
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    ocr = {"text": normalize_ocr_text(ocr_text), "items": []}
    supplied = {
        str(key): str(value).strip()
        for key, value in (metadata or {}).items()
        if value is not None and str(value).strip()
    }
    supplied.update(
        {
            "brand": brand,
            "product_name": product_name,
            "category": normalize_category(category),
            "tone": tone,
        }
    )
    return generate_marketing_copy(ocr, supplied, tone, language=language)


def runtime_ready() -> bool:
    """Check the non-GPU prerequisites before Docker treats Colab as healthy."""
    try:
        import torch
        import rembg  # noqa: F401
        import paddleocr  # noqa: F401
        import diffusers  # noqa: F401
        if not torch.cuda.is_available():
            return False
        tags_url = f"{normalize_ollama_host(OLLAMA_HOST)}/api/tags"
        with urllib.request.urlopen(tags_url, timeout=5) as response:
            tags = json.loads(response.read().decode("utf-8"))
        models = tags.get("models", []) if isinstance(tags, Mapping) else []
        wanted = {
            name.split(":", 1)[0].casefold()
            for name in (OLLAMA_MODEL, *OLLAMA_FALLBACK_MODELS)
        }
        return any(
            str(
                item.get("name", "")
                if isinstance(item, Mapping)
                else getattr(item, "model", getattr(item, "name", ""))
            ).split(":", 1)[0].casefold()
            in wanted
            for item in models
        )
    except Exception:
        return False


def create_app() -> Any:
    from fastapi import FastAPI, File, Form, Request, UploadFile
    from fastapi.responses import JSONResponse, Response

    app = FastAPI(title="Cosmetic AI Colab Service", version=PIPELINE_VERSION)
    @app.middleware("http")
    async def authenticate(request: Request, call_next: Any) -> Any:
        runtime_token = os.getenv("COLAB_AI_TOKEN", os.getenv("AI_SERVICE_TOKEN", "")).strip()
        if runtime_token and request.url.path in {"/health", "/generate-campaign", "/generate-text"}:
            supplied = request.headers.get("authorization", "")
            expected = f"Bearer {runtime_token}"
            if not hmac.compare_digest(supplied, expected):
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Colab bearer authentication failed"},
                    headers={"WWW-Authenticate": "Bearer"},
                )
        return await call_next(request)

    @app.get("/health")
    def health() -> dict[str, Any]:
        try:
            import torch
            gpu = bool(torch.cuda.is_available())
        except ImportError:
            gpu = False
        return {
            "status": "ok",
            "gpu": gpu,
            "ready": bool(gpu and runtime_ready()),
            "runtime_id": RUNTIME_ID,
            "pipeline_version": PIPELINE_VERSION,
            "sdxl_model": SDXL_INPAINT_MODEL,
            "ollama_model": OLLAMA_MODEL,
        }

    @app.post("/generate-campaign")
    async def generate_campaign(
        image: UploadFile = File(...),
        brand: str | None = Form(default=None),
        product_name: str | None = Form(default=None),
        category: str | None = Form(default=None),
        tone: str = Form(default="premium"),
        seed: int = Form(default=42),
        generation_id: str = Form(default=""),
        request_id: str = Form(default=""),
        input_snapshot_hash: str = Form(default=""),
        language: str = Form(default="fr"),
        target_hint: str = Form(default=""),
    ) -> Response:
        image_bytes = await image.read()
        if not image_bytes:
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail="Empty image upload")
        try:
            parsed_target = None
            if target_hint.strip():
                parsed = json.loads(target_hint)
                if not isinstance(parsed, Mapping):
                    raise ValueError("target_hint must be a JSON object")
                parsed_target = normalize_target_box(parsed)
            archive = generate_campaign_zip(
                image_bytes,
                source_name=image.filename or "uploaded_product",
                metadata_overrides={
                    "brand": brand or "",
                    "product_name": product_name or "",
                    "category": category or "",
                    "tone": tone,
                },
                language=language,
                target_box=parsed_target,
                seed=seed,
                request_context={
                    "generation_id": generation_id,
                    "request_id": request_id,
                    "input_snapshot_hash": input_snapshot_hash,
                    "language": language,
                },
            )
        except (PipelineError, ValueError, json.JSONDecodeError) as exc:
            from fastapi import HTTPException
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return Response(
            content=archive,
            media_type="application/zip",
            headers={"Content-Disposition": 'attachment; filename="cosmetique_ai_campaign.zip"'},
        )

    @app.post("/generate-text")
    async def generate_text(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return generate_text_only(
                ocr_text=str(payload.get("ocr_text", "")),
                brand=str(payload.get("brand", "")),
                product_name=str(payload.get("product_name", "")),
                category=str(payload.get("category", "")),
                tone=str(payload.get("tone", "premium")),
                language=str(payload.get("language", "fr")),
                metadata=payload.get("metadata") if isinstance(payload.get("metadata"), Mapping) else None,
            )
        except PipelineError as exc:
            from fastapi import HTTPException
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    return app


try:
    app = create_app()
except ImportError:
    # Local contract tests do not need FastAPI; Colab installs it before serving.
    app = None


_PUBLIC_SERVER_LOCK = threading.Lock()
_PUBLIC_SERVER_THREAD: threading.Thread | None = None
_PUBLIC_SERVER: Any | None = None


def _port_is_listening(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.25):
            return True
    except OSError:
        return False


def run_public_server(port: int = 8000, *, rotate_service_token: bool = False) -> str:
    """Expose the FastAPI app without conflicting with Colab's notebook event loop."""
    try:
        from pyngrok import ngrok
        import uvicorn
    except ImportError as exc:
        raise PipelineError("Install pyngrok and uvicorn before starting the public service") from exc

    token = os.getenv("NGROK_AUTHTOKEN", "").strip()
    if not token:
        raise PipelineError("Set NGROK_AUTHTOKEN before opening the ngrok tunnel")

    service_token = os.getenv("COLAB_AI_TOKEN", os.getenv("AI_SERVICE_TOKEN", "")).strip()
    if rotate_service_token or not service_token:
        import secrets
        service_token = secrets.token_urlsafe(48)
        os.environ["COLAB_AI_TOKEN"] = service_token

    global _PUBLIC_SERVER, _PUBLIC_SERVER_THREAD
    with _PUBLIC_SERVER_LOCK:
        if not _port_is_listening(port):
            config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="info")
            _PUBLIC_SERVER = uvicorn.Server(config)
            _PUBLIC_SERVER_THREAD = threading.Thread(
                target=_PUBLIC_SERVER.run,
                name="cosmetique-colab-api",
                daemon=True,
            )
            _PUBLIC_SERVER_THREAD.start()

    for _ in range(50):
        if _port_is_listening(port):
            break
        time.sleep(0.1)
    else:
        raise PipelineError(f"Colab API did not start on port {port}")

    ngrok.set_auth_token(token)
    # Colab cells survive code reloads.  Without closing an earlier tunnel,
    # ngrok can keep forwarding the public endpoint to a stale Uvicorn port.
    # Always expose exactly the server started by this call.
    for tunnel in ngrok.get_tunnels():
        try:
            ngrok.disconnect(tunnel.public_url)
        except Exception:
            pass
    public_url = ngrok.connect(str(port), "http").public_url
    print(f"Colab API: {public_url}")
    print(f"COLAB_AI_TOKEN: {service_token}")
    return public_url


# %% [markdown]
# Colab installation cell:
#
# !pip install -q --upgrade pillow rembg onnxruntime-gpu paddleocr paddlepaddle #     diffusers transformers accelerate safetensors torch ollama fastapi uvicorn pyngrok
#
# Start Ollama separately in Colab, pull Qwen, then run:
#
# uploaded = files.upload()
# filename, data = next(iter(uploaded.items()))
# archive = generate_campaign_zip(data, source_name=filename)
# Path("/content/cosmetique_ai_campaign.zip").write_bytes(archive)
# files.download("/content/cosmetique_ai_campaign.zip")
#
# For the HTTP service:
# run_public_server(8000)
