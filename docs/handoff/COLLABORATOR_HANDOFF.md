# Historical Campaign Studio V2 Colab handoff

> This document is retained for historical reference only. It is not the
> current runbook and is not required for the accepted extraction platform.
> Use `docs/CONTINUATION_RUNBOOK.md` for the current Docker workflow.

This is the V2 runbook. Do not use the historical V1 poster notebook or its `/health`, `/generate-campaign`, `/generate-text`, Ollama, rembg, or PaddleOCR instructions.

## 1. Runtime

1. Open `notebook/pipeline_ia_cosmetique.ipynb` in Google Colab.
2. Select a T4 GPU and verify `torch.cuda.is_available()` is `True`.
3. Add `GITHUB_TOKEN` or `GH_TOKEN`, optional `HF_TOKEN`, `NGROK_AUTHTOKEN`, and recommended `COLAB_AI_TOKEN` in Colab Secrets.
4. Run the cells in order: bootstrap → source → runtime → smoke → server.
5. Stop if the runtime cell does not report `primary_engine: colab-v2` and `status: ready`.
6. The smoke cell uploads one authorized product image, creates a Product Lock, and only generates after the lock is accepted. A review-required lock is a truthful stop, not a failure to hide.
7. The server cell prints a temporary V2 URL and bearer token and verifies `GET /v2/health`.

The source cell checks out `codex/campaign-studio-upgrade-checkpoint` and installs `ai/requirements-colab.lock`. The lock intentionally excludes the V1 rembg/Paddle/Ollama stack.

## 2. Secrets and boundaries

- `GITHUB_TOKEN`/`GH_TOKEN`: read-only private repository access.
- `HF_TOKEN`/`HUGGINGFACE_TOKEN`: optional Hub authentication for model snapshots.
- `NGROK_AUTHTOKEN`: tunnel authentication.
- `COLAB_AI_TOKEN`: bearer authentication for every V2 endpoint.

The browser or local backend must never receive GitHub or Hub credentials. Keep the temporary URL, bearer token, model cache, logs, and authorized images private.

## 3. What a successful V2 smoke test proves

- Grounding DINO produced a product proposal and contamination checks ran.
- SAM2 produced an immutable mask/cutout lock, or the runtime correctly stopped for review.
- Diffusers generated the environment locally on the Colab GPU.
- The original locked product was restored after generation.
- Exact protected-pixel comparison, duplicate-product detection, and person/hand detection ran before the variant was marked ready.
- `/v2/health` reports the runtime identity and resolved model revisions.

This does not prove the full 213/600/57 dataset acceptance until a batch run and visual review are executed.

## 4. Recovery

A stopped Colab runtime invalidates its URL and runtime identity. Start a new T4 runtime, rerun the notebook from the top, obtain a new URL/token, and update any local V2 client before creating a new job. Never reuse an old job after a runtime change.
