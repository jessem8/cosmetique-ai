# Campaign Studio Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver one French-first Campaign Studio that accepts any supported input image, creates a truthful product lock, generates an elegant background automatically or from an optional user prompt, and exports real output without making Colab or CloseRouter a single point of failure.

**Architecture:** Keep the existing V1 campaign history and storage, but make `/new` the only creation experience. A server-side job pipeline creates immutable product-lock artifacts, chooses an available scene engine, restores original product pixels after any remote edit, and records every stage. A batch runner uses the 213 raw source images as canonical product inputs, the 600 augmented images as regression inputs, and the 57 acceptance images as a held-out visual acceptance set.

**Tech Stack:** React 18 + Vite + React Router + Vitest + Playwright; FastAPI + SQLAlchemy/Alembic + worker; ONNX/rembg CPU fallback; optional Colab GPU adapters; optional CloseRouter server-side provider.

**Spec:** `IMPLEMENTATION_CONTRACT.md`, this plan, and the accepted dataset manifest created in Task 1.

## Global Constraints

- Work only in `C:\gf_ai_task\handoff\remote_update_clone`; preserve the dirty worktree and do not reset, delete, push, deploy, or expose secrets.
- Product inputs are never hard-coded fixtures in the user experience. Fixtures remain in automated tests only.
- A generation may use a remote model only through a server-side allowlist with a preflight credit check, explicit per-job budget, persisted usage, and no automatic paid retry.
- A missing Colab runtime, unavailable provider, insufficient credit, or ambiguous product mask must produce a truthful recoverable state, never a fake result.
- The interface is French throughout, including validation, accessible labels, progress, errors, empty states, and downloads. Copy lives in one catalogue and is tested.
- Product preservation is mandatory: source image -> product lock -> generated/background image -> original-pixel product restoration -> QA -> export.
- The Studio must process every input in the accepted batch manifest and record `ready`, `review_required`, `rejected`, or `failed` with a reason. No image is silently omitted.

---

## File Structure

| Path | Responsibility |
| --- | --- |
| `dataset/manifest.json` | Canonical 213-source, 600-regression, and 57-acceptance dataset inventories with hashes and expected status fields. |
| `backend/app/services/dataset_manifest.py` | Validates files, writes batch rows, and never guesses missing product metadata. |
| `backend/app/services/product_lock_pipeline.py` | Detection, segmentation, hand/person contamination check, refinement, artifact persistence, and status decision. |
| `ai_service/segmentation.py` | Adapter contract for CPU fallback and optional SAM2/D-FINE/Grounding DINO implementations. |
| `backend/app/services/scene_pipeline.py` | Automatic brief generation, optional prompt validation, engine selection, product restoration, and QA. |
| `backend/app/services/provider_policy.py` | Allowlist, credential/credit preflight, budget reservation, no-retry policy, and usage manifest. |
| `backend/app/workers/studio_worker.py` | Executes persisted product-lock and scene jobs instead of returning placeholder variants. |
| `backend/app/api/routes/studio.py` | Upload, lock, correction, job status, comparison, export, and batch APIs. |
| `frontend/src/pages/CampaignStudio.jsx` | The sole creation experience, replacing the V2 demo naming and routes. |
| `frontend/src/studio/*` | French copy, client types, API client, state reducer, accessibility-safe step components, and image comparison UI. |
| `frontend/src/App.jsx` and `frontend/src/components/Navbar.jsx` | Route `/new` to Studio; retain Campaigns only as history. |
| `frontend/src/styles/studio.css` | Tokenized visual system, responsive layout, reduced-motion behaviour, and no duplicate legacy V2 styling. |
| `tests/` and `frontend/src/**/*.test.*` | Contract, dataset, copy, provider-policy, worker, accessibility, and browser tests. |

### Task 1: Freeze a truthful baseline and define the whole-dataset contract

**Files:**
- Create: `dataset/manifest.json`
- Create: `backend/tests/test_dataset_manifest.py`
- Modify: `IMPLEMENTATION_CONTRACT.md`

**Interfaces:**
- Produces `DatasetEntry { id, source_path, sha256, cohort, status }`.
- `cohort` is exactly `canonical_source`, `regression_augmented`, or `visual_acceptance`.

- [ ] **Step 1: Write the failing manifest coverage tests.**

```python
def test_manifest_covers_every_canonical_source(manifest):
    assert len([e for e in manifest if e["cohort"] == "canonical_source"]) == 213
    assert all(e["sha256"] for e in manifest)

def test_manifest_never_promotes_augmented_files_to_catalog_products(manifest):
    assert all(e["cohort"] != "canonical_source" for e in manifest if e["source_path"].endswith("augmentation_manifest.json"))
```

- [ ] **Step 2: Build the manifest from the local dataset with hashes; record missing metadata as `review_required`, not invented names.**

```json
{"id":"p052","source_path":".private_dataset/Stage_1_ouvrier/data/raw/product_photos/p052.jpg","sha256":"<hash>","cohort":"canonical_source","status":"review_required","reason":"metadata row absent"}
```

- [ ] **Step 3: Run `python -m pytest backend/tests/test_dataset_manifest.py -v`; commit only the manifest, test, and contract update.**

### Task 2: Make Product Lock real, batchable, and fail closed

**Files:**
- Modify: `ai_service/segmentation.py`
- Create: `backend/app/services/product_lock_pipeline.py`
- Modify: `backend/app/api/routes/studio.py`
- Create: `backend/tests/test_product_lock_pipeline.py`

**Interfaces:**
- `create_product_lock(source: Path, corrections: LockCorrections) -> ProductLockResult`
- `ProductLockResult` includes `mask_path`, `cutout_path`, `source_hash`, `engine`, `confidence`, `contamination`, `status`, and `reason`.

- [ ] **Step 1: Write failing tests for a high-confidence cutout, an ambiguous mask, and a contaminated hand/person result.**

```python
def test_ambiguous_lock_requires_review(pipeline, ambiguous_fixture):
    lock = pipeline.create_product_lock(ambiguous_fixture, LockCorrections())
    assert lock.status == "review_required"
    assert lock.cutout_path is None

def test_person_contamination_never_autogenerates(pipeline, hand_fixture):
    lock = pipeline.create_product_lock(hand_fixture, LockCorrections())
    assert lock.status == "review_required"
    assert lock.contamination["person_or_hand"] is True
```

- [ ] **Step 2: Implement detector-to-segmenter candidate ranking. Use a CPU model only when available; return `engine=cpu_fallback` honestly. Use SAM2 only after the Colab/adapter health check confirms it.**

```python
if contamination.person_or_hand or best.score < settings.v2_lock_min_confidence:
    return ProductLockResult(status="review_required", reason=contamination.reason, ...)
return persist_lock_artifacts(source, best.mask, engine=engine_name, status="ready")
```

- [ ] **Step 3: Wire positive points, negative points, and selected-box corrections to a new immutable lock revision; do not overwrite the prior lock.**

- [ ] **Step 4: Run unit tests, then a full 213-source batch with a persisted per-image summary. A pass requires every entry to have a status and artifact hashes where `ready`; it does not require all images to be automatically accepted.**

### Task 3: Replace the parallel V2 preview with the French Campaign Studio

**Files:**
- Create: `frontend/src/pages/CampaignStudio.jsx`
- Create: `frontend/src/studio/copy.fr.js`
- Create: `frontend/src/studio/state.js`
- Create: `frontend/src/studio/api.js`
- Create: `frontend/src/studio/components/ProductLockStep.jsx`
- Create: `frontend/src/studio/components/CreativeDirectionStep.jsx`
- Create: `frontend/src/studio/components/GenerationStatus.jsx`
- Create: `frontend/src/studio/components/VariantComparison.jsx`
- Modify: `frontend/src/App.jsx`
- Modify: `frontend/src/components/Navbar.jsx`
- Modify: `frontend/src/styles/studio.css`
- Create: `frontend/src/studio/copy.fr.test.js`

**Interfaces:**
- `StudioState` has exactly `upload`, `lock`, `direction`, `generation`, `comparison`, and `export` states.
- `CreativeDirection = { mode: 'automatic' | 'custom', prompt: string, style: string }`.

- [ ] **Step 1: Write reducer and copy tests. Automatic mode must be ready with an empty prompt; custom mode must require 12–500 non-whitespace characters.**

```javascript
expect(isGenerationReady({ mode: 'automatic', prompt: '' })).toBe(true);
expect(isGenerationReady({ mode: 'custom', prompt: '   ' })).toBe(false);
expect(fr.studio.lock.reviewRequired).toContain('vérification');
```

- [ ] **Step 2: Implement one six-stage flow: Importer, Verrouiller le produit, Direction créative, Générer, Comparer, Exporter. Route `/new` to it; redirect legacy `/studio-v2` paths to `/new` without presenting a V2 label.**

- [ ] **Step 3: Use the visual direction “premium creative production studio”: warm off-white canvas, ink/navy typography, mineral-green accent, large product crop, editorial grid, restrained responsive motion, visible loading/error/retry states, and no fake metrics or fixture variants. Ensure text, aria labels, and error strings use only `copy.fr.js`.**

- [ ] **Step 4: Wire real multipart upload, lock corrections, job polling, real artifact comparison, and download links. Do not render a confidence, provider, QA result, or variant until returned by the API.**

- [ ] **Step 5: Run `npm run lint`, `npm run test:run`, `npm run build`, then Playwright desktop/mobile accessibility checks.**

### Task 4: Implement automatic and custom elite background direction

**Files:**
- Create: `backend/app/services/scene_pipeline.py`
- Modify: `backend/app/api/routes/studio.py`
- Create: `backend/tests/test_scene_pipeline.py`

**Interfaces:**
- `build_scene_brief(lock, direction) -> SceneBrief`
- `generate_scene(lock, brief, policy) -> SceneResult`
- `SceneBrief` records `mode`, `user_prompt`, `automatic_prompt`, `negative_prompt`, and `style`.

- [ ] **Step 1: Write tests proving auto mode builds a prompt from only verified lock/category metadata, custom mode preserves user intent safely, and both include product-protection negatives.**

```python
assert "hands" in brief.negative_prompt
assert "additional products" in brief.negative_prompt
assert result.manifest["mode"] == "automatic"
```

- [ ] **Step 2: Implement automatic prompts using a small, governed style catalogue (editorial daylight, clinical premium, botanical luxury, kinetic colour) rather than unbounded model prose. Put user text after a fixed product-protection prefix.**

- [ ] **Step 3: Always composite/restore the original locked product pixels after a background render, then run QA for product coverage, outside-mask change, dimensions, alpha, and file integrity. A failed QA result is not exportable.**

- [ ] **Step 4: Test automatic and custom directions across the 57-image acceptance cohort. Save result manifests and thumbnails; classify every failure with its stage.**

### Task 5: Make Colab and CloseRouter optional, bounded execution engines

**Files:**
- Create: `backend/app/services/provider_policy.py`
- Modify: `ai_service/provider.py`
- Modify: `backend/app/services/ai_pipeline.py`
- Create: `backend/app/workers/studio_worker.py`
- Modify: `backend/app/core/config.py`
- Create: `backend/tests/test_provider_policy.py`
- Create: `backend/tests/test_studio_worker.py`

**Interfaces:**
- `select_engine(capabilities, requested_engine) -> EngineSelection`
- `preflight_provider(profile, ceiling_micros) -> ProviderDecision`
- `StudioJob` transitions only `queued -> running -> succeeded|review_required|failed`.

- [ ] **Step 1: Write policy tests for missing key, insufficient credit, model not allowlisted, provider 5xx, and spend ceiling reached. Each must leave the Studio usable and must schedule zero automatic paid retries.**

```python
assert preflight_provider(no_key, 200_000).status == "unavailable"
assert select_engine(no_provider, "automatic").engine == "local_or_colab"
assert failed_remote_job.retryable is False
```

- [ ] **Step 2: Implement engine order: healthy Colab GPU service for high-quality segmentation/background work; local CPU product-lock fallback; optional CloseRouter enhancement only when enabled and preflighted. Persist engine, model, request ID, cost, source/output hashes, QA, and timestamps per result.**

- [ ] **Step 3: Package Colab as one reproducible batch notebook/service bootstrap. It loads the pinned models once per runtime, takes the manifest as input, writes artifacts and result manifests, exposes a health endpoint, and never claims GPU/SAM2 use without a positive health response.**

- [ ] **Step 4: Make the worker execute real adapters and artifact storage. Remove user-facing fixture fallbacks; distinguish unavailable, failed, and uncertain completion.**

- [ ] **Step 5: Run worker contract tests with mocked engines, then one no-cost local batch. Run a remote benchmark only after the user has enabled the optional provider and approved its displayed budget.**

### Task 6: Integrate, restart safely, and establish acceptance evidence

**Files:**
- Modify: `docker/docker-compose.yml`
- Modify: `docs/handoff/COLLABORATOR_HANDOFF.md`
- Create: `reports/acceptance/2026-08-22-batch-summary.json`
- Create: `reports/acceptance/2026-08-22-visual-review.md`

- [ ] **Step 1: Start the existing Compose project without deleting volumes. Verify each container’s health endpoint and migration version before opening the browser. A database-only state is a failure, not a successful start.**

- [ ] **Step 2: Run migration, backend, AI, frontend, and browser suites from the current working tree. Address current failures before accepting new results; historical cache output is not evidence.**

- [ ] **Step 3: Through the browser, upload multiple canonical source images, demonstrate one review-required correction, one automatic background, one custom French prompt, one unavailable-provider fallback, comparison, and export. Inspect at desktop and mobile widths.**

- [ ] **Step 4: Run all 213 canonical sources through Product Lock and all 600 regression inputs through the non-destructive regression pass. Publish counts by final status, failure reason, engine, and artifact presence.**

- [ ] **Step 5: Review the 57 held-out acceptance images manually for preserved packaging, no hands/extra objects, French copy, background quality, and no fixture leakage. Delivery is blocked if a product is altered, a hand is accepted automatically, or an output lacks provenance.**

## Acceptance Matrix

| Requirement | Proof |
| --- | --- |
| One integrated Studio | `/new` is the creation entrypoint; no user-facing V2/demo route; Campaigns remains history. |
| All dataset inputs | 213 canonical and 600 regression manifest rows each have a persisted terminal status. |
| Product-only lock | Stored source/mask/cutout hashes; hand/person cases go to manual review, never silent success. |
| Automatic or prompt direction | Auto is executable without text; custom prompt is optional and validated; both persist the actual brief. |
| Elite but truthful backgrounds | Restored product pixels plus QA manifest; failed engines show an honest status. |
| Optional CloseRouter | Provider unavailable/credit exhausted never blocks upload, lock, history, or local/Colab flow. |
| French consistency | Copy-catalog tests plus desktop/mobile browser review. |
| Delivery evidence | Fresh test logs, batch summary, browser screenshots, visual acceptance review, and no claim based on old build artifacts. |

## Decisions Already Made for This Plan

- The 213 raw product photos, not the 600 augmented files, are the canonical set for product processing. Augmented files are regression-only.
- Missing metadata for products 52–213 is surfaced as review work, not guessed content.
- CloseRouter is an opt-in enhancement behind a per-job budget. It is not the default engine and it cannot block the product.
- Colab is a one-runtime batch accelerator, not an assumed permanent dependency. CPU fallback remains honest and functional; unavailable GPU quality is shown as unavailable.
- Existing V1 history/downloads are preserved until the integrated Studio passes browser acceptance; nothing is destructively deleted during recovery.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-08-22-campaign-studio-recovery.md`. Execute it in this session with checkpoints after Tasks 1, 3, and 5; do not begin Task 5’s external provider benchmark without explicit budget approval.
