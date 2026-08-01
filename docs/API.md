# API contract

The browser uses the same-origin website API under `/api/v1`. It never calls
the Colab URL directly.

## Website API

```text
POST /api/v1/auth/register
POST /api/v1/auth/login
POST /api/v1/products
GET  /api/v1/products
GET  /api/v1/products/{product_id}/image

POST /api/v1/products/{product_id}/generations
GET  /api/v1/generations/{generation_id}
GET  /api/v1/generations?cursor=&limit=
GET  /api/v1/generations/{generation_id}/bundle
GET  /api/v1/generations/{generation_id}/artifacts/{artifact_name}
```

Campaign creation performs a bounded authenticated Colab health check. If the
runtime is absent, not ready, or invalid, the request returns
`503 AI_SERVICE_UNAVAILABLE` and does not create a generation.

The worker uploads the immutable product snapshot to Colab, validates the
returned ZIP, stores its assets in the existing private artifact storage, and
stores the strict copy plus OCR metadata in the generation record. The
browser-facing `copy` response remains per-platform for compatibility with
the existing Result screen.

## Colab API

The notebook exposes these authenticated endpoints through ngrok:

```text
GET  /health
POST /generate-campaign
POST /generate-text
```

`/generate-campaign` accepts a multipart `image` upload and optional
`brand`, `product_name`, `category`, `tone`, `seed`, generation/request
identity, input hash, and language fields. It returns a ZIP with the exact
campaign artifact contract.

`/generate-text` accepts OCR text, product metadata, and tone, then returns
the strict JSON copy contract. Ollama/Qwen is only run in Colab; Docker never
loads the model.

## Strict copy contract

```json
{
  "brand": "...",
  "product_name": "...",
  "category": "...",
  "titre": "...",
  "sous_titre": "...",
  "bullets": [],
  "cta": "...",
  "hashtags": [],
  "_meta": {
    "ocr_text": "...",
    "source": "ocr+metadata"
  }
}
```

Placeholder copy, unsupported clinical/medical claims, empty required fields,
and extra root fields are rejected.
