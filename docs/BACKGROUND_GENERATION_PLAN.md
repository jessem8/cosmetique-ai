# Background generation and final-artifact plan

This is the continuation boundary for the project. It is intentionally not
part of the currently accepted extraction workflow.

## Decision

Use an operator-managed ComfyUI installation as a scene-plate generator and
keep the final composition local:

```text
Product Lock source/mask/cutout
          │
          ├── prompt only ──> ComfyUI API-format workflow ──> scene plate
          │
          └── exact cutout + scene plate ──> local compositor ──> final PNG
                                                        │
                                                        └─> hash manifest + QA
```

This follows the research task's recommendation to separate environment
generation from product identity. The extracted product is never sent to the
background provider in this first planned phase. The provider creates a plate;
the local compositor puts the original Product Lock cutout on top.

## Recommended implementation

The next owner can implement a small backend-side adapter with:

- an explicit `ComfyUIConfig` with `enabled=False` by default;
- API-format workflow validation and explicit `node_id.input_name` bindings;
- `POST /prompt` queueing;
- `GET /history/{prompt_id}` polling;
- `GET /view` image retrieval and image-byte validation;
- fail-closed handling for disabled providers, node errors, failed jobs, bad
  responses, and timeouts.

The next owner can implement a separate local compositor with:

- `BackgroundGenerationPlan` with Product Lock hashes, prompt, canvas, seed,
  and normalized placement;
- deterministic placement and local RGBA composition;
- final PNG output with the product layer composited after the scene;
- a manifest containing source, mask, cutout, scene, and final SHA-256 hashes;
- measured QA for opaque product-pixel mismatches.

The current repository does not add these modules, settings, routes, or
containers. Its provider registry still exposes only
`product-extraction/native-sam2`, so no UI route or worker path can silently
invoke this future integration.

## Why ComfyUI is external and opt-in

ComfyUI is a separate graph-based generation application. Its documented API
uses an API-format workflow submitted to `/prompt`, then exposes history and
outputs through `/history/{prompt_id}` and `/view`.

- [Official ComfyUI API examples](https://github.com/comfy-org/docs/blob/main/development/comfyui-server/api-examples.mdx)
- [Official ComfyUI API workflow format](https://github.com/comfy-org/docs/blob/main/development/api-development/workflow-api-format.mdx)
- [ComfyUI repository and license](https://github.com/comfy-org/ComfyUI)

The repository does not install a checkpoint, invent a model filename, or add
a heavy generation container to the low-GPU extraction stack. The operator
must export an API-format workflow from the chosen ComfyUI installation and
accept that model's license and memory requirements separately.

## Enabling a controlled experiment

Only after the extraction path is accepted on the target machine:

1. Run ComfyUI separately and verify its local API is reachable from the
   backend container.
2. Export a workflow in **API format** from that ComfyUI instance.
3. Copy the workflow into a private, untracked server-side path and set
   `COMFYUI_WORKFLOW_PATH` to that path inside the backend/worker container.
4. Set `COMFYUI_BASE_URL` and, if configured, `COMFYUI_API_TOKEN` in the
   server-side environment only.
5. Set `BACKGROUND_GENERATION_ENABLED=1` only in an isolated development
   profile. Do not add the provider to the production allowlist until manual
   visual acceptance exists.
6. Bind the exported workflow's actual positive prompt, negative prompt, seed,
   width, and height fields using explicit server-side configuration. The
   adapter should reject unknown fields instead of silently using a different
   node.
7. Start from an already accepted Product Lock. Generate only the scene
   plate, then call the local compositor with the original lock's mask and
   cutout bytes.
8. Store the returned manifest next to the final PNG and inspect both the
   image and the measured `opaque_product_pixel_mismatches` value.

Never use a generated image as a replacement Product Lock. Never infer
success from an HTTP 200, a completed ComfyUI job, or a plausible-looking
background alone.

## Optional later harmonization

The research task identified `libcom` as a possible later composition-quality
layer for placement, harmonization, shadow, and reflection. It is not wired
into this commit because the first acceptance gate is exact product-pixel
preservation. If added later, it must operate on a derived scene/shadow layer
and preserve the original cutout bytes as the final product layer. The same
manifest and opaque-pixel QA gate remains mandatory.

## Acceptance gate for a future phase

A future generation change is not complete until all of the following are
true:

- a real ComfyUI workflow/model revision is recorded;
- one real accepted Product Lock is used;
- the scene plate and final PNG are stored as distinct artifacts;
- the final manifest binds all input/output hashes and the workflow revision;
- opaque product-pixel mismatches are zero, or the result is marked
  `needs_review`;
- a human confirms that no hand/background object was reintroduced;
- the extraction-only test suite and low-GPU startup still pass.
