# Private evaluation protocol

Evaluation data is private, authorized, and excluded from Git, Docker build
contexts, logs, and generated documentation.

## Dataset composition

Curate exactly 24 original cosmetics or personal-care product photographs:

- 12 standard cases with a single, clearly visible product.
- 12 difficult cases covering multiple products, reflective or transparent
  packaging, low contrast, occlusion, small targets, and complex boundaries.
- A target bounding box for every original, expressed against the
  EXIF-corrected image.
- At least 12 expert-reviewed binary masks, including difficult boundary cases.

Keep a stable opaque sample ID. Do not place customer names, direct identifiers,
or unneeded image metadata in annotations.

## Frozen split and change control

Freeze the 24-image acceptance set before model comparison. Do not remove failed
samples or tune thresholds against the acceptance result without recording a new
dataset version and rerunning every candidate. Model selection compares the
existing baseline with Grounding DINO Tiny plus SAM Base on the same set.

BiRefNet remains disabled unless its pinned revision and custom code are reviewed
and the crop-level fallback is explicitly enabled. RMBG-2.0 is not a default V1
candidate. No fine-tuning or LoRA is allowed unless this evaluation demonstrates
a measured need and a separate change is approved.

## Required metrics

| Gate | Acceptance |
| --- | --- |
| Automatic target selection | At least 90% correct over all 24 images |
| Ambiguity recovery | 100% recoverable through candidate-box selection |
| Expert-mask median IoU | At least 0.90 |
| Expert-mask median boundary F1 | At least 0.85 |
| Unsupported claims | Zero across every generated platform copy |
| Product identity | No changed pixels inside the eroded accepted mask |
| Final dimensions | Exact 1080×1080, 1200×630, and 1200×627 |

Report median mask metrics together with per-sample values. Averages alone can
hide catastrophic failures. Record detection selection, ambiguity candidates,
mask QA result, seed, model revision, runtime ID, and pipeline version for each
sample.

## Timing

Measure separately:

- First-time bootstrap: dependency and model downloads.
- Cached cold generation: new process with pinned snapshots already present,
  target no more than 15 minutes.
- Warm generation: loaded/reusable runtime, target no more than 5 minutes.

Do not fold provider download time into cached cold inference, and do not omit it
from the first-time bootstrap report.

## Review procedure

1. Validate image rights and strip unneeded metadata.
2. Lock annotations and dataset version.
3. Run each benchmark candidate with deterministic seeds.
4. Review automatic selections and ambiguity previews.
5. Compare accepted masks with expert masks.
6. Validate pixel preservation, exact artifacts, checksums, and copy evidence.
7. Record every failure without replacing or silently skipping the sample.
8. Publish only aggregate results and opaque sample IDs.

A release may report the evaluation gate as passed only when all 24 authorized
images and the required expert masks were actually executed.

