# V1 API contract

The browser uses the same-origin website API under `/api/v1`. It never calls the
Colab URL directly.

## Authentication and products

```text
POST /api/v1/auth/register
POST /api/v1/auth/login
POST /api/v1/products
GET  /api/v1/products
GET  /api/v1/products/{product_id}
GET  /api/v1/products/{product_id}/image
```

Product creation uses `multipart/form-data` with required `image`, `name`, and
`category`, plus optional `brand`. The server checks the byte limit, decoded
pixel limit, magic format, declared MIME, and nonblank bounded metadata before
placing the original in private storage.

## Create a campaign

```text
POST /api/v1/products/{product_id}/generations
Idempotency-Key: <opaque 16..128 printable ASCII characters>
```

```json
{
  "language": "fr",
  "seed": 42,
  "audience": "adultes à la peau sensible",
  "benefits": ["Hydrate la peau"],
  "ingredients": ["Aloe vera"],
  "verified_claims": ["Testé sous contrôle dermatologique"],
  "cta": "Découvrir",
  "creative_direction": "Studio minéral, lumière douce",
  "target_hint": null,
  "source_generation_id": null
}
```

Unknown fields are rejected. Optional text and arrays are bounded and
deduplicated. A target box, when present, is finite, positive, normalized, and
fully inside the EXIF-corrected image.

The backend combines product facts and campaign input into an immutable
canonical snapshot, including the source image fingerprint and dimensions. The
same key and snapshot replays the original response. Reusing a key with changed
input returns `409 IDEMPOTENCY_CONFLICT`.

Creation performs a bounded authenticated Colab health check. An unavailable or
unexpected runtime returns `503 AI_SERVICE_UNAVAILABLE` without inserting a
generation.

## Generation state

```text
GET /api/v1/generations/{generation_id}
GET /api/v1/generations?cursor=&limit=
```

```json
{
  "id": "uuid",
  "product_id": "uuid",
  "product": {
    "name": "Sérum Exemple",
    "brand": "Marque Exemple",
    "category": "soin_visage"
  },
  "source_generation_id": null,
  "status": "processing",
  "stage": "background",
  "completed_stages": ["analysis", "extraction", "art_direction"],
  "language": "fr",
  "seed": 42,
  "attempt_count": 1,
  "created_at": "RFC3339",
  "updated_at": "RFC3339",
  "error": null,
  "ambiguity": null,
  "copy": null,
  "artifacts": null
}
```

Lifecycle:

```text
pending → processing → done | error
```

Real stages:

```text
analysis → extraction → art_direction → background
→ composition → copy → export → packaging
```

There is no percentage, queue position, countdown, or estimated completion time.

History cursors are opaque and stable across equal timestamps. `limit` is from 1
through 50. Missing and non-owned generations both return `404`.

## Ambiguous targets

`TARGET_AMBIGUOUS` is a terminal error on the original generation and includes a
protected original URL plus normalized candidates:

```json
{
  "error": {
    "code": "TARGET_AMBIGUOUS",
    "message": "Plusieurs produits possibles ont été détectés."
  },
  "ambiguity": {
    "original_url": "/api/v1/products/{product_id}/image",
    "candidates": [
      {
        "id": "candidate-1",
        "score": 0.91,
        "x": 0.1,
        "y": 0.2,
        "width": 0.3,
        "height": 0.6
      }
    ]
  }
}
```

Selection creates a complete new campaign with a new idempotency key,
`source_generation_id`, and:

```json
{
  "target_hint": {
    "type": "box",
    "x": 0.1,
    "y": 0.2,
    "width": 0.3,
    "height": 0.6
  }
}
```

The backend inherits and revalidates the original verified brief. The failed
generation is immutable.

## Completed copy and artifacts

Completed `copy` has one language and independent evidence-linked platform
payloads:

```json
{
  "language": "fr",
  "instagram": {
    "text": "Publication validée",
    "hashtags": ["#Exemple"],
    "claims": [
      {
        "evidence_id": "benefit-1",
        "rendered_text": "Hydrate la peau"
      }
    ]
  },
  "facebook": {
    "text": "Publication validée",
    "hashtags": [],
    "claims": []
  },
  "linkedin": {
    "text": "Publication validée",
    "hashtags": [],
    "claims": []
  }
}
```

Artifact metadata points only to protected allowlisted routes:

```text
GET /api/v1/generations/{id}/artifacts/{artifact_name}
GET /api/v1/generations/{id}/bundle
```

The bundle endpoint streams the already-validated atomic ZIP and never rebuilds
a partial archive.

## Colab service

Every Colab endpoint requires the server-side bearer token:

```text
GET  /v1/health
POST /v1/jobs
GET  /v1/jobs/{job_id}
GET  /v1/jobs/{job_id}/bundle
```

Health reports readiness, runtime identity, GPU facts, exact pinned model
revisions, and pipeline version. The backend binds a website generation to the
preflight runtime ID; any later change fails with `AI_RUNTIME_LOST`.

## Stable error codes

```text
AI_SERVICE_UNAVAILABLE
AI_RUNTIME_LOST
REMOTE_AUTH_FAILED
REMOTE_PROTOCOL_ERROR
TARGET_NOT_FOUND
TARGET_AMBIGUOUS
EXTRACTION_FAILED
MASK_QUALITY_FAILED
CLAIM_SAFETY_FAILED
INVALID_GENERATION_REQUEST
IDEMPOTENCY_CONFLICT
ARTIFACT_CONTRACT_FAILED
ARTIFACT_CHECKSUM_MISMATCH
ARTIFACT_STORAGE_FAILED
GENERATION_STALE
INTERNAL_ERROR
```

Browser messages are stable and localized. Remote bodies, credentials, local
paths, tracebacks, and exception strings are logged only in sanitized form.

