# Campaign artifact contract

The worker accepts a campaign only after validating the complete ZIP as one
atomic result. Partial assets are never promoted as a successful generation.

## Exact bundle

The ZIP contains these eight root-level members exactly once:

```text
instagram.jpg
facebook.jpg
linkedin.jpg
copy.json
cutout.png
mask.png
background.jpg
manifest.json
```

Directories, duplicate names, path traversal, alternate separators, symlinks,
encryption, extra fields, nested archives, suspicious compression ratios, and
oversized members are rejected before extraction.

## Images

- `instagram.jpg`: RGB JPEG, exactly 1080×1080.
- `facebook.jpg`: RGB JPEG, exactly 1200×630.
- `linkedin.jpg`: RGB JPEG, exactly 1200×627.
- `cutout.png`: RGBA product cutout at the EXIF-corrected source dimensions.
- `mask.png`: single-channel accepted mask at the same dimensions.
- `background.jpg`: generated background without the source product.

The source product is never regenerated. Platform finals are deterministic
compositions of the accepted cutout and generated background. Product pixels
inside the eroded accepted mask must match the source.

## Copy

`copy.json` contains exactly one campaign language and independent Instagram,
Facebook, and LinkedIn copy. Every rendered marketing claim carries evidence
references into the verified input ledger. The payload is rejected if it
contains malformed JSON, unknown fields, unsupported facts, or unsafe claims.

## Manifest

`manifest.json` binds the result to:

- generation, request, and runtime IDs;
- canonical immutable-input hash, seed, language, and target selection;
- pinned dependencies and exact Hugging Face repository revisions;
- an ordered completion receipt for every real pipeline stage;
- MIME type, byte length, SHA-256, dimensions, and semantic role for each of the
  seven non-manifest artifacts.

The website validates each member against the manifest, computes a separate
SHA-256 for the final ZIP, and atomically stores the validated bundle. Browser
artifact routes are allowlisted and authenticated.

