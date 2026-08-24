# Cosmetique AI — clean continuation runbook

This is the short, canonical guide for running the current project on
Windows and continuing the work later. The remote repository's target branch
is `main`.

## What is finished

The accepted application workflow is product extraction only:

1. Upload a real JPG, PNG, or WebP.
2. Create a Product Lock with a source, binary mask, transparent cutout, and
   provenance.
3. Inspect the mask and cutout.
4. Optionally paint a correction, cancel a draft, or save/recalculate a new
   immutable revision.
5. Follow the real job status until it is terminal.
6. Validate/save the extracted product.
7. Stop. Background generation is not part of the current UI.

The accepted GPU path uses native Grounding DINO + SAM2 when the installed
CUDA stack can run it. The lower-GPU/CPU path uses ONNX U2Net and does not
reserve a GPU. The current runtime does not load SDXL or CloseRouter.

## First run

Prerequisites:

- Windows 10/11 with Docker Desktop and the Compose v2 plugin;
- Git;
- an NVIDIA driver only if using the native GPU profile;
- the private environment file supplied separately. Never commit it.

From the repository root:

```powershell
Copy-Item docker/.env.example docker/.env
# Edit docker/.env and replace every required placeholder.
docker compose --env-file docker/.env -f docker/docker-compose.yml up -d --build
```

For an MX350, Pascal-class GPU, or CPU-only machine, use the explicit CPU
override:

```powershell
docker compose --env-file docker/.env -f docker/docker-compose.yml -f docker/docker-compose.cpu.yml up -d --build
```

The CPU command is the safest choice for a machine that shows sustained 100%
CPU or cannot execute the native CUDA model. It may be slower, but it avoids
loading the large native stack and removes the inherited GPU reservation.

Open `http://localhost/new` or `http://localhost:5173/new`, depending on the
port configured in `docker/.env`, then sign in and select **Extraire un
produit**.

## Check the real state

```powershell
docker compose --env-file docker/.env -f docker/docker-compose.yml ps
docker compose --env-file docker/.env -f docker/docker-compose.yml logs --tail=100 ai-runtime
docker compose --env-file docker/.env -f docker/docker-compose.yml logs --tail=100 backend
```

`migrations` is a one-shot successful container. It exits after `alembic
upgrade head`; it is not supposed to stay green/running. The backend and
worker depend on its successful exit.

The engine-status card is meaningful only when it reports both the engine
mode and the current job phase. A healthy HTTP endpoint means the service is
reachable; it does not prove the mask is correct. Inspect the actual mask and
cutout.

## Tests before handing work to someone else

```powershell
python -m pytest ai/tests -q

Push-Location backend
$env:PYTHONPATH = ".;.."
python -m pytest tests -q --basetemp=.pytest-tmp-handoff
Pop-Location

Push-Location frontend
npm run test:run
npm run lint
npm run build
Pop-Location

docker compose --env-file docker/.env -f docker/docker-compose.yml config --quiet
docker compose --env-file docker/.env -f docker/docker-compose.yml -f docker/docker-compose.cpu.yml config --quiet
git diff --check
```

## Continue background work only as a separate phase

Read `docs/BACKGROUND_GENERATION_PLAN.md`. It is only a possible direction for
the next owner, not an implementation or commitment. Research alternative
providers and workflows further before choosing one. No ComfyUI client,
provider configuration, or final-artifact route is enabled in the current
project. Do not put a future provider token in frontend files, do not send the
extracted product to a generator, and do not mark a background output ready
without the manifest and human review.

## Repository rules

- Work on remote `main` only.
- Keep `docker/.env`, backend `.env`, model caches, test artifacts, and real
  customer images out of Git.
- Do not revive old Colab/SDXL/CloseRouter paths because they appear in
  historical files.
- Treat `needs_review`, unavailable, timeout, and unknown provider completion
  as truthful states; never turn them into a fake success.
- The remote branch cleanup is intentional. If a future contributor needs a
  short-lived branch, merge it into `main` and delete it afterward.
