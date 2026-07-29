# Dataset guide

Cosmetique AI V1 does not train, fine-tune, create a LoRA, or augment source
photos. The only project dataset is a private, authorized 24-image evaluation set
used to measure target selection, segmentation, product-pixel preservation, exact
exports, and claim safety.

Use `data/evaluation/metadata.example.jsonl` as the schema and store real images,
masks, and metadata only under `data/evaluation/private/`. That directory is
ignored locally. Source images must remain original; do not resize, recolor, strip
their metadata in place, or generate synthetic variants.

Each record needs an immutable SHA-256, decoded EXIF-corrected dimensions, MIME,
normalized target box, difficulty class, authorization receipt, and optional
reviewed mask path. The complete scoring and acceptance rules are in
`AI_EVALUATION_PROTOCOL.md`.

Training may be proposed only after the locked evaluation demonstrates a specific
quality gap that cannot be recovered by user target selection or a reviewed
segmentation fallback. Such a proposal requires a separate data-rights, licensing,
privacy, compute, and acceptance review.
