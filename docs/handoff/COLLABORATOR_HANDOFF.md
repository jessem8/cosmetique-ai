# Cosmetique AI — collaborator handoff

This is the current, from-scratch runbook for the private Cosmetique AI V1
repository. It is intentionally the only handoff document you need to follow.
The website is local Docker software; image generation runs in a temporary,
authenticated Google Colab GPU runtime.

## 1. What you are running

```text
browser → local Nginx/React → FastAPI + PostgreSQL + worker
                                      ↓ authenticated HTTPS
                              Colab T4 + ngrok
                                      ↓ validated ZIP
                              private local artifacts
```

Docker does not load image-generation or language models. The Colab pipeline preserves
the photographed product, generates the scene around it, writes claim-safe
copy, and returns one validated campaign ZIP. A missing or unhealthy Colab
runtime is an explicit failure; it is never silently replaced by a CPU or fake
success.

## 2. What you need

- Git access to the private repository: `https://github.com/jessem8/cosmetique-ai`
- Docker Desktop with Compose v2
- A Google account with Colab access
- Permission to the authorized evaluation images in the shared Drive folder:
  [Cosmetique AI evaluation set](https://drive.google.com/drive/folders/1bU0jg8Yfm1qba1dgGF1QlNPyi4l5TOsx?usp=drive_link)
- One authorized product image for a smoke test

The Drive folder is the source of private evaluation data; the images are
intentionally not in Git. Do not copy the private folder into the repository or
commit its images, masks, metadata, or generated ZIPs.

## 3. Get the exact source

Clone the delivery branch (the notebook refreshes this branch explicitly when a
Colab runtime is reused):

```powershell
git clone --branch codex/colab-first-repair --single-branch https://github.com/jessem8/cosmetique-ai.git
Set-Location .\cosmetique-ai
```

The canonical execution files are:

- `notebook/pipeline_ia_cosmetique.ipynb` — the Colab entry point
- `ai/colab_cosmetic_poster_pipeline.py` — the Colab API and pipeline
- `ai/campaign_core.py` — deterministic copy and rendering rules
- `ai/requirements-colab.txt` — Colab dependency reference

For the local website, keep the repository's `backend/`, `frontend/`,
`ai_core/`, and `docker/` directories together. `DATASET_GUIDE.md`,
`AI_EVALUATION_PROTOCOL.md`, and `data/evaluation/metadata.example.jsonl`
describe the private evaluation protocol without containing private images.

## 4. Start Colab first

1. Open `notebook/pipeline_ia_cosmetique.ipynb` in Google Colab.
2. Select **Runtime → Change runtime type → T4 GPU**, then verify that a GPU
   is actually attached (the runtime type label alone is not proof):

   ```python
   import torch
   print(torch.cuda.is_available())
   if torch.cuda.is_available():
       print(torch.cuda.get_device_name(0))
   ```

3. In **Colab → Secrets**, add the following values. Use the secret names
   exactly; do not paste them into notebook cells or commit them.

   | Secret | Purpose |
   | --- | --- |
   | `GITHUB_TOKEN` | Fine-grained read access used only when the private repo must be cloned in Colab. The notebook also accepts `GH_TOKEN`. |
   | `NGROK_AUTHTOKEN` | Opens the temporary ngrok HTTPS tunnel. |
   | `COLAB_AI_TOKEN` | Bearer token checked by `/health`, `/generate-campaign`, and `/generate-text`. Generate a new value for each runtime. |

   Generate a high-entropy service token outside the notebook, for example:

   ```powershell
   python -c "import secrets; print(secrets.token_urlsafe(48))"
   ```

4. Choose **Runtime → Run all**. The notebook installs dependencies, refreshes
   the `codex/colab-first-repair` checkout under `/content/cosmetique-ai`,
   starts Ollama on its loopback CPU endpoint for copy preflight, and imports
   the canonical pipeline. Wait for `COPY PREFLIGHT PASSED`.
5. The upload cell can be used for a direct ZIP smoke test. Select one
   authorized image from the Drive folder and upload it when prompted. The
   defaults (`Rexona`, `Shower Fresh`, `deodorant`) are only an acceptance
   fixture; replace them with the image's verified metadata for other products.
6. The final cell starts the authenticated API on a fresh child process and
   port. It prints a temporary ngrok URL and the service token. Keep both
   private. The URL and token are valid only while this Colab runtime and
   tunnel remain alive.

### GPU availability caveat

Colab free sessions do not guarantee a T4 (or any GPU), and a session can be
pre-empted after it starts. If `torch.cuda.is_available()` is `False`, stop and
retry with another runtime or an account/plan that provides a GPU. Do not run
the website against that session: `runtime_ready()` and the Colab health
contract require CUDA, and a CPU run is not a valid acceptance result. A
runtime replacement also changes the URL, runtime ID, and bearer token; repeat
the Colab steps and update Docker rather than reusing old credentials.

## 5. Configure and start Docker

From the repository root, create the ignored local environment file:

```powershell
Copy-Item .\docker\.env.example .\docker\.env
```

Edit `docker/.env` locally and replace every `replace_...` value:

- `DB_USER`, `DB_NAME`, and `DB_PASSWORD` must match the `DATABASE_URL` user,
  password, and database. The URL-safe token generated above needs no URL
  encoding.
- `SECRET_KEY` is a separate website JWT secret.
- `COLAB_AI_URL` is the current ngrok URL printed by the final notebook cell.
- `AI_SERVICE_TOKEN` must exactly match the Colab `COLAB_AI_TOKEN` secret.
- If `APP_PORT` is changed, set `APP_ORIGIN` to the matching origin (for
  example, `http://localhost:8080`).

Never commit `docker/.env`, print its contents, or paste authorization headers
into an issue or chat. The checked-in `.env.example` files are templates only.

Validate and start the stack with the checked-in Compose file:

```powershell
docker compose --env-file .\docker\.env -f .\docker\docker-compose.yml config
docker compose --env-file .\docker\.env -f .\docker\docker-compose.yml up --build -d
docker compose --env-file .\docker\.env -f .\docker\docker-compose.yml ps
```

Keep the `name:` and service/network/volume declarations in
`docker/docker-compose.yml` unchanged. The Compose file applies migrations
before the API and worker become healthy, keeps PostgreSQL on the internal
network, and binds the browser only to `127.0.0.1:${APP_PORT:-80}`. Open
`http://localhost` (or the configured port) and check the Nginx health endpoint:

```powershell
Invoke-WebRequest http://localhost/healthz | Select-Object StatusCode
```

The backend's `/health` endpoint is an internal container health check; the
browser-facing API remains under `/api/v1`.

## 6. Run one end-to-end campaign

1. Register and sign in at the local site.
2. Upload an authorized product image from the Drive set. Keep the original
   file unchanged; EXIF normalization and private storage happen server-side.
3. Enter only verified brand/product/category facts. If the target selector
   shows more than one candidate, choose the photographed product.
4. Start a French or English generation and wait for the worker timeline to
   finish. The browser talks only to the local `/api/v1` API; the Colab URL and
   bearer token stay server-side.
5. Download the completed bundle from the Result screen. A successful result
   contains exactly these nine root-level members:

   ```text
   instagram.jpg   1080×1080 RGB JPEG
   facebook.jpg    1200×630 RGB JPEG
   linkedin.jpg    1200×627 RGB JPEG
   cutout.png      RGBA product-isolation evidence
   mask.png        single-channel L mask evidence
   background.jpg  1024×1024 RGB generated background
   copy.json       strict copy contract
   ocr.json        OCR text and confidence evidence
   manifest.json   model, seed, runtime, hash, and provenance binding
   ```

   The worker validates names, MIME types, dimensions, checksums, request
   identity, language, and product-pixel preservation before storing any file.
   Partial or diagnostic-only ZIPs are rejected. See `docs/ARTIFACTS.md` for
   the complete contract.

## 7. Runtime recovery and troubleshooting

Inspect only bounded, sanitized logs:

```powershell
docker compose --env-file .\docker\.env -f .\docker\docker-compose.yml logs --tail 200 backend worker
```

| Symptom or code | Action |
| --- | --- |
| `/healthz` is not `200` | Check `docker compose ... ps`; wait for migrations and backend health, then inspect the bounded logs. |
| `AI_SERVICE_UNAVAILABLE` | Colab health preflight failed. Confirm the runtime has a GPU, rerun the notebook from the top, and update the URL/token. |
| `AI_RUNTIME_LOST` | The runtime identity changed during a job. Treat the old job as failed; reconnect a new runtime and create a new generation. |
| `REMOTE_AUTH_FAILED` or Colab `401` | `AI_SERVICE_TOKEN` and `COLAB_AI_TOKEN` differ, or an old tunnel is in use. Copy the current values without quotes and recreate the API and worker. |
| `TARGET_AMBIGUOUS` | Select the displayed product candidate and start a new generation. |
| `CLAIM_SAFETY_FAILED` | Correct the brief to contain only verified facts; do not bypass the claim gate. |
| `ARTIFACT_CONTRACT_FAILED` | Keep the failed job immutable; inspect bounded worker logs and the Colab API log, then retry with a new request. |
| `GENERATION_STALE` | The bounded remote deadline expired. Check Colab and create a new generation. |
| Notebook says `Runtime preflight failed` | Re-run the Ollama cell only, confirm `COPY PREFLIGHT PASSED`, then rerun the upload/API cells. |
| Notebook cannot find the canonical pipeline | Set the `GITHUB_TOKEN` Colab Secret (or `GH_TOKEN`) for the private clone, or place this checkout under `/content`; rerun the source cell. |

When replacing a Colab runtime, repeat all of these steps: create a new
service token, run the notebook from the top, copy the new URL and token to
`docker/.env`, then recreate only the backend and worker:

```powershell
docker compose --env-file .\docker\.env -f .\docker\docker-compose.yml up -d --force-recreate backend worker
```

Do not expose `/content/cosmetique_ai_api.log`, notebook output, tokens, or
private image data in a bug report. Rotate any credential that was ever pasted
into a public location; deleting it from the working tree is not rotation.

## 8. Verification and handoff boundaries

The repository's checked-in tests and docs are the local contract:

```powershell
python -m pytest ai/tests
Set-Location .\backend
python -m pytest tests
Set-Location ..
python -c "import json, nbformat; p='notebook/pipeline_ia_cosmetique.ipynb'; nb=nbformat.read(p, as_version=4); nbformat.validate(nb); print('notebook valid')"
docker compose --env-file .\docker\.env -f .\docker\docker-compose.yml config
```

Install the corresponding requirements before running each Python suite
(`ai/requirements-colab.txt` for the AI tests and `backend/requirements-dev.txt`
for the backend tests). Run frontend checks from `frontend/` when needed
(`npm ci`, `npm run lint`, `npm run test:run`, and `npm run build`). A real fresh-T4 generation against
the authorized Drive images is an operator acceptance gate; it cannot be
proven by CPU tests or by assuming that Colab assigned a GPU. Record actual
timings and preserve no private image contents in the repository.

The minimum delivery is the source branch plus the canonical notebook, `ai/`,
`backend/`, `frontend/`, `ai_core/`, `docker/`, and the safe dataset/evaluation
templates. Generated campaigns, private evaluation files, environment files,
model caches, and tokens are runtime data—not delivery artifacts.
