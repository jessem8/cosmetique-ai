# Artifact contracts

## Current Product Lock artifacts

The supported extraction workflow stores the source through the product
record and stores each immutable Product Lock revision with a real binary
`mask.png` and transparent `cutout.png`, plus hashes, dimensions, revision
number, model/runtime provenance, and status. These artifacts are private and
served only through the authenticated Product Lock routes documented in
`docs/API.md`.

The current workflow stops after the Product Lock is inspected and validated.
It does not require a background image, campaign ZIP, SDXL result, or
CloseRouter result.

## Historical campaign bundle contract

The bundle below belongs to the older generation/Colab compatibility path. It
is retained so legacy tests and historical artifacts remain understandable;
it is not the active product-extraction workflow.

The worker accepts a campaign only after validating the complete ZIP as one
atomic result. Partial assets are never promoted as a successful generation.

## Required members (pipeline 1.2.0)

```text
instagram.jpg
facebook.jpg
linkedin.jpg
cutout.png
mask.png
background.jpg
copy.json
ocr.json
manifest.json
```

The cutout, mask, and background are evidence-bearing members rather than
optional diagnostics. A bundle missing any of the nine members is not promoted.

All members must be root-level, unique, unencrypted, under the size limits, and
free of path traversal or unknown filenames.

## Images

- `instagram.jpg`: RGB JPEG, exactly 1080×1080.
- `facebook.jpg`: RGB JPEG, exactly 1200×630.
- `linkedin.jpg`: RGB JPEG, exactly 1200×627.
- `cutout.png`: RGBA PNG.
- `mask.png`: single-channel L PNG.
- `background.jpg`: RGB JPEG, exactly 1024×1024.

The historical bundle was designed around an older generator and is not
executed by the current extraction runtime. Do not treat its `background.jpg`
or `preserves_product_pixels` field as evidence that background generation is
currently available.

## Copy and OCR

`copy.json` must match the strict copy contract and contain OCR evidence in
`_meta.ocr_text`. Placeholder copy and unsupported clinical/medical claims
are rejected.

`ocr.json` contains the normalized label text and confidence items. It is
stored with the generation metadata for traceability.

## Manifest

`manifest.json` binds the result to the pipeline version, runtime ID,
generation/request IDs, immutable input hash, language, seed, selected models,
and the product-pixel preservation flag. The backend compares request identity
before installing any asset.
