# Cosmetique AI

Private, reproducible product-ad studio for cosmetics and personal care.
Cosmetique AI preserves the photographed product, generates only the background,
and exports a complete evidence-bound campaign for Instagram, Facebook, and
LinkedIn.

## What V1 delivers

- A French React interface with one French or English campaign per generation.
- Exact RGB JPEG exports: Instagram `1080×1080`, Facebook `1200×630`, and
  LinkedIn `1200×627`.
- Platform-specific copy constrained to the product facts supplied by the user.
- Evidence views for the original, mask, cutout, generated background, and final
  composition.
- A durable FastAPI/PostgreSQL job worker and private local artifact storage.
- An authenticated asynchronous Colab GPU API exposed through a temporary
  Cloudflare Quick Tunnel.
- A strict eight-member ZIP with model revisions, stage receipts, dimensions,
  MIME types, sizes, and checksums.

There is no fake progress, silent fallback, queue-position promise, or degraded
result marked as successful.

## Architecture

```text
Browser
  │ same-origin /api/v1
  ▼
Nginx ── FastAPI ── PostgreSQL queue
                     │
                     ▼
                   Worker
                     │ authenticated /v1 polling
                     ▼
             Colab T4 + Quick Tunnel
                     │
                     ▼
            validated atomic ZIP bundle
                     │
                     ▼
             persistent local volume
```

The browser never receives the Colab URL or bearer token. Colab receives image
bytes and a validated request, never database or storage credentials.

## Repository layout

```text
ai_core/   Installable AI pipeline, lazy adapters, Colab API, validation, tests
backend/   FastAPI API, PostgreSQL models, worker, private storage, migrations
frontend/  React 18 / Vite French product interface
notebook/  Fresh-T4 Run-all Colab notebook
docker/    PostgreSQL, migration, API, worker, Nginx/frontend Compose stack
data/      Private-evaluation templates; authorized images remain ignored
docs/      Operations, evaluation, artifact, and security runbooks
```

## Start locally

1. Start the authenticated Colab service and obtain its temporary Quick Tunnel
   URL and bearer token.
2. Copy `docker/.env.example` to the ignored `docker/.env`.
3. Replace every required value with a unique high-entropy secret.
4. From the repository root:

   ```powershell
   docker compose --env-file .\docker\.env -f .\docker\docker-compose.yml config
   docker compose --env-file .\docker\.env -f .\docker\docker-compose.yml up --build -d
   docker compose --env-file .\docker\.env -f .\docker\docker-compose.yml ps
   ```

5. Open `http://localhost:<APP_PORT>`.

Full launch, verification, runtime-loss recovery, shutdown, and troubleshooting
instructions are in [docs/OPERATIONS.md](docs/OPERATIONS.md).

## Development checks

AI core:

```powershell
cd ai_core
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
.\.venv\Scripts\python.exe -m pytest -q
```

Backend:

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
```

Frontend:

```powershell
cd frontend
npm ci
npm run lint
npm run test:run
npm run build
npm run test:e2e
```

GPU model imports are lazy; CPU contract tests do not download or load model
weights.

## API surface

Website API:

```text
POST /api/v1/products
GET  /api/v1/products/{product_id}/image
POST /api/v1/products/{product_id}/generations
GET  /api/v1/generations/{id}
GET  /api/v1/generations/{id}/bundle
GET  /api/v1/generations/{id}/artifacts/{artifact_name}
GET  /api/v1/generations?cursor=&limit=
```

Authenticated Colab API:

```text
GET  /v1/health
POST /v1/jobs
GET  /v1/jobs/{id}
GET  /v1/jobs/{id}/bundle
```

Generation creation requires an opaque `Idempotency-Key`. The lifecycle is
`pending → processing → done | error`, with only objectively completed stages
reported.

## Model policy

- Grounding DINO Tiny for candidate detection.
- SAM Base for segmentation.
- SDXL Base for the background only.
- Qwen 2.5 7B Instruct in 4-bit for structured copy after SDXL unload.
- BiRefNet disabled by default and allowed only as a reviewed, pinned crop-level
  fallback.
- No RMBG-2.0 default, fine-tuning, or LoRA in V1.

Every production model revision is pinned and written into the manifest.

## Documentation

- [Operations and Colab recovery](docs/OPERATIONS.md)
- [V1 API contract](docs/API.md)
- [Private evaluation protocol](docs/EVALUATION.md)
- [Artifact and manifest contract](docs/ARTIFACTS.md)
- [Security and privacy operations](SECURITY.md)

## Release truth

A gate is accepted only with execution evidence. In particular, a fresh Colab
T4 **Run all**, the authorized 24-image private evaluation, provider-side
credential rotation, and the private GitHub push are never inferred from local
unit tests.
