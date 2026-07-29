# Cosmetique AI — explanation for Jesse

Date: 2026-07-29  
Audience: you (so you can talk about the project without reading the whole thread)

## One sentence

You built a **local cosmetics ad studio**: French website → FastAPI/Postgres worker → Google Colab T4 GPU over a temporary Cloudflare tunnel → a strict 8-file ZIP campaign.

## Why it exists

Your girlfriend’s Stage_1_ouvrier internship project needed a coherent product: preserve the real product photo, generate only the background, write claim-safe copy, export IG/FB/LinkedIn sizes, and fail honestly when GPU/Colab dies.

## Layers (what Codex + this finish pass produced)

| Layer | Folder | Role |
|---|---|---|
| AI core | `ai_core/` | Shared pipeline + Colab service API + ZIP/manifest rules |
| Backend | `backend/` | Auth, products, generations, worker, storage, migrations |
| Frontend | `frontend/` | French Cool Pearl UI, ambiguity picker, polling, evidence |
| Notebook | `notebook/` | Fresh T4 “Run all” Colab entrypoint |
| Docker | `docker/` | Postgres + API + worker + Nginx frontend |

## What the tests prove (today)

- **67** ai_core tests, **119** backend tests, **51** frontend unit tests, **15** Playwright E2E scenarios, lint + production build.
- Docker stack comes up healthy on your machine (DB, migrations, API, worker, frontend).
- Four Hugging Face models are pinned and Hub-visible (Grounding DINO Tiny, SAM Base, SDXL Base, Qwen 2.5 7B Instruct).

Details: `handoff/verification-evidence.md`.

## What she must still do (immediately after handoff)

1. Open Colab with a **T4**, run the notebook, paste tunnel URL + bearer token into `docker/.env`.
2. Run a real generation.
3. Optional school gate: private 24-image evaluation (authorized images only).

You cannot finish the official T4 gate on your RTX 5050 (~8 GiB); the project requires ≥12 GiB sequential VRAM.

## How to explain it in under a minute

“We ship a private Docker website. The browser only talks to our API. A worker sends the photo to an authenticated Colab GPU service through a short-lived Cloudflare URL. Colab runs detection → mask → background → composition → copy → export, returns a checksummed ZIP. If Colab dies, the job fails explicitly—no fake success.”

## Repo

Private GitHub: `https://github.com/jessem8/cosmetique-ai` (after push).  
Working tree: `C:\gf_ai_task\fully_cloned\Stage_1_ouvrier` on branch `codex/implementation`.

## Fixes landed in this finish pass

- Orchestrator tests updated for background QA validator.
- Colab service concurrency test uses a distinct request id.
- Frontend Docker healthcheck fixed (`127.0.0.1` vs broken IPv6 `localhost`).
