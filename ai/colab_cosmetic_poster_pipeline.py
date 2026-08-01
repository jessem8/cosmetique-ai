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
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from PIL import Image, ImageOps

try:
    from .campaign_core import (
        FORMATS,
        build_campaign_zip,
        build_inpaint_prompt,
        normalize_category,
        normalize_ocr_text,
        render_poster,
        validate_marketing_copy,
    )
except ImportError:
    from campaign_core import (
        FORMATS,
        build_campaign_zip,
        build_inpaint_prompt,
        normalize_category,
        normalize_ocr_text,
        render_poster,
        validate_marketing_copy,
    )


SDXL_INPAINT_MODEL = os.getenv(
    "SDXL_INPAINT_MODEL",
    "diffusers/stable-diffusion-xl-1.0-inpainting-0.1",
)
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b-instruct-q4_K_M")
PIPELINE_VERSION = "1.1.0"
RUNTIME_ID = os.getenv("COLAB_RUNTIME_ID", f"colab-runtime-{os.getpid()}")

CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "deodorant": ("deodor", "motion sense", "motionsense", "anti-transpir", "shower fresh"),
    "perfume": ("parfum", "perfume", "eau de parfum", "eau de toilette", "idôle", "idole", "nectar"),
    "makeup": ("palette", "mascara", "lipstick", "gloss", "blush", "highlighter", "eyeshadow"),
    "skincare": ("serum", "creme", "crème", "gel moussant", "seb iaclear", "hydra", "spf"),
    "haircare": ("shampoo", "shampooing", "conditioner", "huile capillaire", "hair"),
    "sunscreen": ("spf", "sunscreen", "solaire", "anthelios", "uvmune"),
    "nailcare": ("dissolvant", "ongles", "nail"),
    "bodycare": ("lait corporel", "body wash", "body butter", "baume corporel", "handcreme"),
}

_COPY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "brand": {"type": "string"},
        "product_name": {"type": "string"},
        "category": {"type": "string"},
        "titre": {"type": "string"},
        "sous_titre": {"type": "string"},
        "bullets": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
        "cta": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
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


def read_product_label(image: Image.Image) -> dict[str, Any]:
    try:
        import numpy as np
        from paddleocr import PaddleOCR
    except ImportError as exc:
        raise PipelineError(
            "PaddleOCR is required in Colab. Install paddleocr and paddlepaddle first."
        ) from exc

    # French is a Latin-language model and covers the English/French labels in the dataset.
    engine = PaddleOCR(
        lang="fr",
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
    )
    enlarged = image.resize((image.width * 2, image.height * 2), Image.Resampling.LANCZOS)
    array = np.asarray(enlarged)

    if hasattr(engine, "predict"):
        result = engine.predict(array)
    elif hasattr(engine, "ocr"):
        result = engine.ocr(array, cls=True)
    else:
        raise PipelineError("Unsupported PaddleOCR API: no predict or ocr method available")

    items: list[dict[str, Any]] = []
    for item in result if isinstance(result, list) else [result]:
        items.extend(_parse_ocr_result(item))

    if not items:
        raise PipelineError("PaddleOCR found no readable product label text")

    text = normalize_ocr_text(" ".join(item["text"] for item in items))
    return {"text": text, "items": items}


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
        _RMBG_SESSION = new_session("isnet-general-use")

    cutout = remove(
        image.convert("RGBA"),
        session=_RMBG_SESSION,
        alpha_matting=True,
        alpha_matting_foreground_threshold=240,
        alpha_matting_background_threshold=10,
        alpha_matting_erode_size=10,
        post_process_mask=True,
    ).convert("RGBA")
    mask = cutout.getchannel("A")
    if mask.getbbox() is None:
        raise PipelineError("rembg returned an empty product mask")
    return cutout, mask


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
    max_width = int(canvas_size[0] * 0.43)
    max_height = int(canvas_size[1] * 0.78)
    scale = min(max_width / trimmed.width, max_height / trimmed.height)
    product = trimmed.resize(
        (max(1, int(trimmed.width * scale)), max(1, int(trimmed.height * scale))),
        Image.Resampling.LANCZOS,
    )

    position = (
        int(canvas_size[0] * 0.72 - product.width / 2),
        int(canvas_size[1] * 0.54 - product.height / 2),
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
        "magenta studio, unrelated props, distorted object, warped package"
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
    ).images[0]
    return result.convert("RGB")


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
) -> dict[str, Any]:
    try:
        from ollama import Client
    except ImportError as exc:
        raise PipelineError(
            "The ollama Python package is required and Ollama must expose Qwen2.5."
        ) from exc

    ocr_text = normalize_ocr_text(str(ocr.get("text", "")))
    user_prompt = f"""
Produit: {metadata["brand"]} {metadata["product_name"]}
Catégorie: {metadata["category"]}
Ton: {tone}
Texte réellement lu sur l'étiquette:
{ocr_text}

Retourne uniquement l'objet JSON demandé.
Le champ titre doit être une ligne créative courte.
Le sous_titre et les bullets ne peuvent utiliser que des informations compatibles avec le texte OCR.
N'invente aucune promesse médicale, clinique, dermatologique, aucun résultat chiffré absent de l'étiquette et aucun ingrédient absent.
"""
    system_prompt = (
        "Tu es un copywriter cosmétique senior. Réponds uniquement avec un JSON conforme au schéma. "
        "Les textes doivent être courts, élégants et lisibles sur mobile. "
        "Ne produis jamais de marque ou de produit de démonstration."
    )

    client = Client(host=OLLAMA_HOST)
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            response = client.chat(
                model=OLLAMA_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                format=_COPY_SCHEMA,
                stream=False,
                options={"temperature": 0, "seed": 42},
            )
            raw = _ollama_content(response)
            payload = json.loads(raw)
            return validate_marketing_copy(payload, ocr_text, metadata)
        except Exception as exc:
            last_error = exc
            user_prompt += (
                f"\nLa réponse précédente était invalide ({exc}). "
                "Réponds à nouveau avec les champs obligatoires, sans texte hors JSON."
            )
            if attempt == 0:
                continue

    raise PipelineError(f"Qwen/Ollama did not produce valid copy: {last_error}")


def _image_from_bytes(value: bytes) -> Image.Image:
    return Image.open(io.BytesIO(value)).convert("RGB")


def generate_campaign_zip(
    image_bytes: bytes,
    *,
    source_name: str = "uploaded_product",
    metadata_overrides: Mapping[str, str] | None = None,
    seed: int = 42,
    request_context: Mapping[str, Any] | None = None,
) -> bytes:
    image = preprocess_image(image_bytes)
    ocr = read_product_label(image)
    metadata = infer_product_metadata(ocr, metadata_overrides)
    cutout, product_mask = remove_product_background(image)
    base, inpaint_mask, product, product_position = prepare_inpainting_inputs(cutout)
    scene = generate_environment(base, inpaint_mask, metadata["category"], seed=seed)
    copy = generate_marketing_copy(ocr, metadata, metadata["tone"])

    # The renderer receives the original rembg pixels, never the SDXL package.
    posters = {
        platform: render_poster(
            scene,
            product,
            copy,
            platform,
            source_product_position=product_position,
        )
        for platform in FORMATS
    }
    context = dict(request_context or {})
    manifest = {
        "pipeline_version": PIPELINE_VERSION,
        "runtime_id": RUNTIME_ID,
        "generation_id": str(context.get("generation_id", "")),
        "request_id": str(context.get("request_id", "")),
        "input_snapshot_hash": str(context.get("input_snapshot_hash", "")),
        "language": str(context.get("language", "fr")),
        "source_name": source_name,
        "category": metadata["category"],
        "models": {
            "background_removal": "isnet-general-use",
            "ocr": "PaddleOCR-lang-fr",
            "background_generation": SDXL_INPAINT_MODEL,
            "copy_generation": OLLAMA_MODEL,
        },
        "seed": seed,
        "preserves_product_pixels": True,
        "text_rendering": "Pillow",
    }
    diagnostics = {
        "cutout.png": cutout,
        "mask.png": product_mask,
        "background.jpg": scene,
    }
    return build_campaign_zip(posters, copy, ocr, manifest, diagnostics=diagnostics)


def generate_text_only(
    ocr_text: str,
    brand: str,
    product_name: str,
    category: str,
    tone: str = "premium",
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
    return generate_marketing_copy(ocr, supplied, tone)


def create_app() -> Any:
    from fastapi import FastAPI, File, Form, Request, UploadFile
    from fastapi.responses import JSONResponse, Response

    app = FastAPI(title="Cosmetic AI Colab Service", version=PIPELINE_VERSION)
    runtime_token = os.getenv("COLAB_AI_TOKEN", os.getenv("AI_SERVICE_TOKEN", "")).strip()

    @app.middleware("http")
    async def authenticate(request: Request, call_next: Any) -> Any:
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
            "ready": gpu,
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
    ) -> Response:
        image_bytes = await image.read()
        if not image_bytes:
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail="Empty image upload")
        try:
            archive = generate_campaign_zip(
                image_bytes,
                source_name=image.filename or "uploaded_product",
                metadata_overrides={
                    "brand": brand or "",
                    "product_name": product_name or "",
                    "category": category or "",
                    "tone": tone,
                },
                seed=seed,
                request_context={
                    "generation_id": generation_id,
                    "request_id": request_id,
                    "input_snapshot_hash": input_snapshot_hash,
                    "language": language,
                },
            )
        except PipelineError as exc:
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


def run_public_server(port: int = 8000) -> str:
    try:
        from pyngrok import ngrok
        import uvicorn
    except ImportError as exc:
        raise PipelineError("Install pyngrok and uvicorn before starting the public service") from exc

    token = os.getenv("NGROK_AUTHTOKEN", "").strip()
    if not token:
        raise PipelineError("Set NGROK_AUTHTOKEN before opening the ngrok tunnel")

    ngrok.set_auth_token(token)
    public_url = ngrok.connect(str(port), "http").public_url
    print(f"Colab API: {public_url}")
    uvicorn.run(app, host="0.0.0.0", port=port)
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
