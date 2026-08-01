# Cosmetique AI

Colab-first cosmetic campaign generation for product photos. The photographed
product is removed with rembg, its label is read with PaddleOCR, a category-aware
SDXL inpainting scene is generated around the preserved product, and all copy is
rendered deterministically with Pillow.

## Canonical pipeline

The source of truth is:

- `ai/colab_cosmetic_poster_pipeline.py`
- `ai/campaign_core.py`
- `notebook/pipeline_ia_cosmetique.ipynb`

The Google Drive clone and the Downloads notebook/ZIP are reference material.
They are not the delivery target.

Pipeline:

```text
image → EXIF normalization → PaddleOCR → rembg isnet-general-use
      → SDXL Inpainting around preserved product → Qwen2.5 strict JSON
      → Pillow typography → three platform exports → ZIP
```

SDXL is forbidden from drawing text, logos, packaging, extra products, vases,
fruit, people, hands, floating objects, or a generic magenta studio. The source
product is composited back after generation and receives a deterministic contact
shadow.

## Output contract

Each campaign ZIP contains:

- `instagram.jpg` — 1080×1080
- `facebook.jpg` — 1200×630
- `linkedin.jpg` — 1200×627
- `copy.json` — strict brand/product/category/titre/sous-titre/bullets/CTA/hashtags contract
- `ocr.json` — OCR text and confidence values
- `manifest.json` — models, category, seed, and product-pixel preservation flag

Diagnostic cutout, mask, and background images may also be included.

## Colab API

Run the notebook on a Colab GPU runtime. It exposes:

```text
GET  /health
POST /generate-campaign
POST /generate-text
```

Set `NGROK_AUTHTOKEN` before running the final notebook cell. The cell prints
the temporary ngrok URL and a generated `COLAB_AI_TOKEN`; copy both into the
Docker environment as `COLAB_AI_URL` and `AI_SERVICE_TOKEN`.

## Website and Docker

Docker remains model-free. The existing FastAPI/PostgreSQL worker uploads the
product to Colab, validates the returned ZIP, stores the assets in its private
artifact volume, and exposes the existing frontend asset URLs. When Colab is
missing or unavailable, the generation becomes an explicit error.

```powershell
docker compose --env-file .\\docker\\.env -f .\\docker\\docker-compose.yml config
docker compose --env-file .\\docker\\.env -f .\\docker\\docker-compose.yml up --build -d
```

The database and worker were retained to avoid a schema/frontend rewrite; no
local AI fallback is used.

## Verification

Run focused Python tests for `ai/` and `backend/`, validate the notebook with
`nbformat.validate`, and run `docker compose config`. GPU acceptance is
performed in Colab using real dataset images, including Rexona and Lancôme, then
the website is tested with a different arbitrary image.

## Repository layout

```text
ai/         canonical Colab pipeline and deterministic rendering
notebook/   canonical Colab runner
backend/    website API, queue worker, ZIP validation, private storage
frontend/   existing product interface
docker/     PostgreSQL, API, worker, and frontend Compose stack
ai_core/    schema-only compatibility package for the website backend
```
