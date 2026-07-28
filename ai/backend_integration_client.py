from __future__ import annotations

import httpx


async def call_colab_ai_service(
    *,
    service_url: str,
    image_bytes: bytes,
    filename: str,
    product_name: str,
    brand: str = "",
    category: str = "",
    tone: str = "luxe",
    palette: str = "blue, white, clean premium",
    mood: str = "fresh elegant atmosphere",
    seed: int = 42,
    timeout_seconds: int = 300,
) -> bytes:
    """
    Call the Colab FastAPI service and return the ZIP bytes.

    service_url example:
        https://xxxx.ngrok-free.app

    Returned ZIP contains:
        affiche_instagram.jpg
        affiche_facebook.jpg
        affiche_linkedin.jpg
        metadata.json
    """
    endpoint = service_url.rstrip("/") + "/generate-campaign"
    files = {
        "image": (filename, image_bytes, "image/jpeg"),
    }
    data = {
        "product_name": product_name,
        "brand": brand,
        "category": category,
        "tone": tone,
        "palette": palette,
        "mood": mood,
        "seed": str(seed),
    }

    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        response = await client.post(endpoint, files=files, data=data)
        response.raise_for_status()
        return response.content
