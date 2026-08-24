# Product extraction platform — current handoff

**Checkout:** `C:\gf_ai_task\handoff\remote_update_clone`
**Remote branch:** `main`
**Scope:** extraction only; no background generation, SDXL, or CloseRouter execution
**Secrets:** local `docker/.env` is not committed

## Delivered workflow

1. Upload a real product photo.
2. Store the source through the backend.
3. Run Product Lock extraction.
4. Display the source, binary mask, transparent cutout, confidence, revision, and provenance.
5. Use the explicit correction mode to paint a contaminating hand/object, cancel drafts when needed, and save recalculation as a new immutable revision.
6. Follow the live job status through recalculation, then choose **Enregistrer le produit extrait** only after inspection.
7. Validate the Product Lock only when the server has real mask and cutout artifacts.
8. Stop. There is no generation step in the active UI or provider registry.

## Runtime behavior

- Capable NVIDIA path: native Grounding DINO proposals plus the native SAM2 image predictor.
- Lower-GPU path: `docker/docker-compose.cpu.yml` selects `Dockerfile.ai-cpu`, clears the inherited GPU reservation, and runs CPU U2Net through ONNX Runtime; its model initialization is also backgrounded.
- Automatic GPU path: `GPU_RUNTIME_MODE=auto` probes CUDA safely and falls back to CPU extraction when the GPU or CUDA architecture cannot run the native path.
- Startup no longer scans all historical Product Locks; an explicit lock ID restores its artifacts on demand. Large-mask topology checks are bounded for CPU responsiveness while source, mask, and cutout artifacts remain full resolution.
- The active GPU image lock no longer installs or eagerly loads a background-generation model.
- The backend provider registry exposes only `product-extraction/native-sam2`; background-generation capability is false.
- The engine-status API reports `native-sam2`, `cpu-u2net`, or an unavailable state so the frontend does not present a false ready state.
- The UI reports the job phase separately from engine readiness, caps brush corrections below the runtime contract, supports cancel/save, and exposes recalculation errors as correction errors rather than generation errors.

## Optional continuation architecture

The repository contains a researched ComfyUI scene-plate and local-composite
plan in `docs/BACKGROUND_GENERATION_PLAN.md`; it is intentionally not
implemented or registered as a provider. The current accepted workflow
remains extraction-only. When the next phase is started, it should generate a
text-only scene plate and then composite the Product Lock cutout locally; the
generator must not repaint the product.

## Manual acceptance

Start the stack with one of these commands:

```powershell
# NVIDIA-capable machine
docker compose --env-file docker/.env -f docker/docker-compose.yml up -d --build

# MX350/Pascal-class or CPU-only machine
docker compose --env-file docker/.env -f docker/docker-compose.yml -f docker/docker-compose.cpu.yml up -d --build
```

Then open `http://localhost:5173/new`, upload a real source image, run extraction, and inspect the mask and transparent cutout. The expected stopping point is the extraction evidence screen. Do not test generation routes; they are intentionally disabled.

## Evidence rule

A healthy container, passing unit tests, or a returned HTTP response is not visual acceptance. The manual check must confirm that the complete product is present in the cutout and that the binary mask does not retain only a tiny marker or unrelated background.

The repository’s previous failed generation artifacts are historical evidence only and are not part of this workflow. The canonical continuation and operator instructions are in `docs/CONTINUATION_RUNBOOK.md`.
