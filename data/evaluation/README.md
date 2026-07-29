# Private V1 evaluation set

Place the authorized evaluation images under `private/images/` and reviewed masks
under `private/masks/`. Everything below `private/` is locally ignored; never add
customer images, generated previews, or annotations containing personal data to
source control.

V1 uses exactly 24 original product photographs:

- 12 standard cases with one clear cosmetics or personal-care product.
- 12 difficult cases covering clutter, reflections, transparency, small products,
  multiple products, weak contrast, unusual angles, or partial occlusion.
- Every image has a reviewed target box.
- At least 12 images have an expert-reviewed binary target mask.

Copy `metadata.example.jsonl` to `private/metadata.jsonl`, create one record per
image, and follow `../../AI_EVALUATION_PROTOCOL.md`. Do not augment, fine-tune, or
train on this set in V1.
