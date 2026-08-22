# Cosmetique AI V2 operations

The active Colab integration is Campaign Studio V2. The historical V1 poster service is not an accepted runtime for the new Studio.

## Colab

Open `notebook/pipeline_ia_cosmetique.ipynb`, select a T4 GPU, add the V2 Colab Secrets, and run the cells in order. The final cell starts an authenticated temporary ngrok tunnel for the `/v2/*` API. A healthy response must contain `primary_engine: colab-v2`, `status: ready`, a runtime ID, and resolved model revisions.

## Secrets

- `GITHUB_TOKEN` or `GH_TOKEN`: private checkpoint clone, read-only.
- `HF_TOKEN` or `HUGGINGFACE_TOKEN`: optional Hugging Face authentication.
- `NGROK_AUTHTOKEN`: temporary tunnel.
- `COLAB_AI_TOKEN`: V2 bearer token.

Do not put these values in Git, screenshots, notebook source, or browser-visible configuration.

## V2 API checks

```powershell
$headers = @{ Authorization = 'Bearer <COLAB_AI_TOKEN>' }
Invoke-RestMethod '<COLAB_V2_URL>/v2/health' -Headers $headers
```

Expected state is `status=ready` and `primary_engine=colab-v2`. A missing model, missing GPU, expired runtime, or failed provider must be surfaced as unavailable/review-required; it must never become a fake successful campaign.

## V2 request flow

```text
source image → Grounding DINO proposal/contamination check → SAM2 Product Lock
             → optional correction revision → structured scene brief
             → local Diffusers background edit → original product restoration
             → pixel/duplicate/person/hand QA → variant manifest/export
```

`POST /v2/batches` returns one terminal row per submitted image: `ready`, `needs_review`, or `failed`. No image is silently skipped.

## Runtime recovery

A new Colab runtime changes the URL and runtime ID. Stop using the old endpoint, start the notebook from the top, update the V2 client URL/token, and create new jobs. Do not retry a job whose remote completion is unknown.
