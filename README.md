# Cosmetique AI — Campaign Studio V2

Campaign Studio V2 is the product-preserving cosmetics image engine. Its Colab entry point loads the pinned Grounding DINO, SAM2, and Diffusers snapshots once per GPU runtime, creates immutable product locks, restores the original product pixels after scene generation, runs fail-closed QA, and exposes authenticated `/v2/*` endpoints.

## V2 source of truth

- `notebook/pipeline_ia_cosmetique.ipynb` — V2-only Colab bootstrap and smoke test
- `ai/colab_v2_runtime.py` — model registry, proposal consensus, SAM2 lock engine
- `ai/colab_v2_service.py` — real Transformers/Diffusers adapters, V2 API, and batch service
- `ai_service/` — immutable lock, scene, provider, restoration, and QA contracts
- `frontend/`, `backend/`, `docker/` — website and durable storage layers

The historical V1 poster module remains in the repository only for compatibility with old records and tests. The V2 notebook, V2 Colab API, and V2 smoke path do not import or execute it.

## Colab secrets

Add these in Colab **Secrets**; never paste them into cells or commit them:

- `GITHUB_TOKEN` or `GH_TOKEN`: fine-grained read access to the private checkpoint branch.
- `HF_TOKEN` or `HUGGINGFACE_TOKEN`: optional for public snapshots; required if a model license/account requires Hub authentication.
- `NGROK_AUTHTOKEN`: temporary HTTPS tunnel authentication.
- `COLAB_AI_TOKEN`: recommended high-entropy bearer token for `/v2/*`; the notebook generates one if absent.

Select a T4 GPU and run the notebook cells in order. The runtime cell must print `primary_engine: colab-v2` and `status: ready`. The smoke cell must produce an accepted lock before generation; ambiguous or contaminated inputs stop for correction instead of generating a fake result. The final cell starts the authenticated V2 API and verifies `/v2/health`.

## V2 API

```text
GET  /v2/health
POST /v2/product-locks
GET  /v2/product-locks/{lock_id}
POST /v2/product-locks/{lock_id}/refinements
GET  /v2/product-locks/{lock_id}/artifacts/{artifact}
POST /v2/generations
GET  /v2/generations/{generation_id}
GET  /v2/generations/{generation_id}/variants/{variant_index}/image
POST /v2/batches
```

Every request is bearer-authenticated when `COLAB_AI_TOKEN` is configured. The health response reports the runtime ID, resolved model revisions, device, and optional-model failures.

## Local verification

```powershell
python -m pytest ai/tests
python -c "import nbformat; p='notebook/pipeline_ia_cosmetique.ipynb'; nb=nbformat.read(p, as_version=4); nbformat.validate(nb); print('notebook valid')"
```

A fresh T4 run with authorized product images is still an external acceptance gate. CPU tests prove contracts and fake-adapter behavior; they do not prove that Colab downloaded the real snapshots or produced acceptable images.
