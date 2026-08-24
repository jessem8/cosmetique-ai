# Cosmetique AI — product extraction workspace

This branch delivers one supported workflow: upload a product photo, create a real Product Lock, inspect the binary mask and transparent cutout, optionally mark a correction area, and validate the extraction. The active application stops after extraction evidence. Background generation and external image-provider calls are disabled. Correction and save actions are explicit, cancellable, and return a new immutable revision.

## Active path

- `frontend/` — authenticated React workspace at `/new`
- `backend/` — authenticated product upload, Product Lock, refinement, validation, artifact, and engine-status APIs
- `ai/gpu_runtime.py` — private extraction runtime with explicit readiness gates
- `ai/gpu_service.py` — native Grounding DINO + SAM2 image extraction and CPU U2Net fallback; historical locks restore only when requested
- `ai_service/` — image, mask, cutout, provenance, and storage contracts
- `docker/docker-compose.yml` — CUDA runtime for capable NVIDIA GPUs
- `docker/docker-compose.cpu.yml` — CPU-only runtime override for MX350/Pascal-class or CPU-only machines
- `docs/BACKGROUND_GENERATION_PLAN.md` — researched, optional architecture plan for the next phase

The older generation/Colab modules remain only as repository compatibility material. They are not imported by the active frontend or the extraction runtime.

## Runtime choices

For a capable NVIDIA GPU:

```powershell
docker compose --env-file docker/.env -f docker/docker-compose.yml up -d --build
```

For an MX350 or a machine where CUDA is not usable:

```powershell
docker compose --env-file docker/.env -f docker/docker-compose.yml -f docker/docker-compose.cpu.yml up -d --build
```

The CPU override uses ONNX Runtime with U2Net and explicitly resets the base NVIDIA reservation. It does not load the native SAM2/Grounding DINO stack; U2Net initializes in the background so the API binds immediately.

The GPU runtime is adaptive in `GPU_RUNTIME_MODE=auto`: it uses the native image-only detector/segmenter when the installed CUDA stack can execute it, otherwise it reports the reason and uses the CPU extraction engine. No generation model is loaded on this path.

## Optional background-generation integration

The recommended continuation architecture is deliberately separate from the
accepted extraction path:

```text
accepted Product Lock cutout + mask
        │
        ├── text-only scene prompt → operator's ComfyUI API-format workflow
        │                             → background scene plate
        │
        └── local deterministic compositor → final PNG + hash manifest
                                                        │
                                                        └─ QA
```

The next phase can use the documented ComfyUI `/prompt`,
`/history/{prompt_id}`, and `/view` contract to create a text-only scene
plate, then place the immutable cutout last in a local compositor. That work
is intentionally not implemented here: the next owner chooses the workflow,
model, provider configuration, and acceptance procedure. The current
provider allowlist remains extraction-only.

Read [the background-generation plan](docs/BACKGROUND_GENERATION_PLAN.md)
before enabling anything, then research and validate that proposal further
before implementation. Use [the continuation runbook](docs/CONTINUATION_RUNBOOK.md)
for the current extraction workflow and lower-GPU commands.

## Manual test

1. Open `http://localhost:5173` and sign in, or use the existing local session.
2. Open **Extraire un produit**.
3. Choose a real JPG, PNG, or WebP product image and enter its name.
4. Select **Créer le Product Lock** / **Extraire le produit**.
5. Inspect the source, mask overlay, transparent cutout, confidence, revision, and model provenance.
6. Choose **Corriger le masque**, paint only the defective area, then either cancel the draft or choose **Enregistrer et recalculer**. The correction is capped and sent as a new Product Lock revision.
7. Follow the live job state through recalculation and inspect the new revision.
8. Choose **Enregistrer le produit extrait** to validate the extraction. The workflow intentionally stops there.

A successful request must leave real server artifacts for the source, mask, and cutout. Startup does not scan or decode every historical artifact, and high-resolution topology checks use a bounded analysis copy while the persisted mask/cutout remain full resolution. A healthy container alone is not a claim that the segmentation is visually correct.

## Verification

```powershell
# from the repository root
python -m pytest ai/tests -q

# backend tests need both backend and shared packages on PYTHONPATH
Push-Location backend
$env:PYTHONPATH = ".;.."
python -m pytest tests -q --basetemp=.pytest-tmp
Pop-Location

# frontend
Push-Location frontend
npm run test:run
npm run lint
npm run build
Pop-Location

docker compose --env-file docker/.env -f docker/docker-compose.yml config --quiet
docker compose --env-file docker/.env -f docker/docker-compose.yml -f docker/docker-compose.cpu.yml config --quiet
git diff --check
```

Do not commit local `docker/.env`, model caches, or generated artifacts containing secrets.
