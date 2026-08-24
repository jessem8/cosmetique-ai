# API contract

## Current supported extraction API

The current `/new` workspace uses the authenticated API below. Product Lock
creation and refinement are the only supported AI workflow. The browser never
receives the private AI runtime URL or bearer token.

```text
POST /api/v1/auth/register
POST /api/v1/auth/login
POST /api/v1/products
GET  /api/v1/products/{product_id}
GET  /api/v1/products/{product_id}/image

GET  /api/v1/studio/v2/engine/status
GET  /api/v1/studio/v2/provider-profiles
POST /api/v1/studio/v2/product-locks
GET  /api/v1/studio/v2/product-locks?product_id=&limit=
GET  /api/v1/studio/v2/product-locks/{revision_id}
GET  /api/v1/studio/v2/product-locks/{revision_id}/artifacts/mask.png
GET  /api/v1/studio/v2/product-locks/{revision_id}/artifacts/cutout.png
POST /api/v1/studio/v2/product-locks/{revision_id}/refine
POST /api/v1/studio/v2/product-locks/{revision_id}/validate
POST /api/v1/studio/v2/product-locks/{revision_id}/reject
```

The `provider-profiles` response exposes the server-owned
`product-extraction/native-sam2` profile. The engine-status response reports
the actual `native-sam2` or `cpu-u2net` mode and runtime phases. A healthy
status response is not visual mask acceptance; inspect the returned mask and
cutout.

Generation endpoints remain present for compatibility with older clients,
but the active provider advertises `supports_scene_generation=false` and a
generation request fails closed with `BACKGROUND_GENERATION_DISABLED`. There
is no current background, SDXL, CloseRouter, or external image-provider call.

## Historical compatibility API

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
