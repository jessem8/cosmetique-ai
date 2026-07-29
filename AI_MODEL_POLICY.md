# AI model and runtime policy

Cosmetique AI accepts only immutable Hugging Face commit revisions:

| Role | Repository | Revision | License |
|---|---|---|---|
| Detection | `IDEA-Research/grounding-dino-tiny` | `a2bb814dd30d776dcf7e30523b00659f4f141c71` | Apache-2.0 |
| Segmentation | `facebook/sam-vit-base` | `70c1a07f894ebb5b307fd9eaaee97b9dfc16068f` | Apache-2.0 |
| Empty background | `stabilityai/stable-diffusion-xl-base-1.0` | `462165984030d82259a11f4367a4eed129e94a7b` | OpenRAIL++ |
| Evidence selection | `Qwen/Qwen2.5-7B-Instruct` | `a09a35458c702b33eeacc393d103063234e8bc28` | Apache-2.0 |

The product is never generated or retouched. SDXL creates only an empty
background; accepted source pixels are composed deterministically and checked
again after verified text is rendered.

Qwen cannot author campaign prose. It may select and order at most three evidence
IDs per platform. The core renders fixed French or English platform templates
around verbatim user evidence and rejects any byte-level semantic deviation from
that deterministic result. User-provided facts must already be written in the
chosen campaign language; V1 has no translation path.

BiRefNet revision `e2bf8e4460fc8fa32bba5ea4d94b3233d367b0e4` remains disabled. It contains
custom model code and may appear in a manifest only after an explicit review plus
a durable `sha256:<64 hex>` review receipt. RMBG-2.0 is excluded from V1.

GPU adapters are lazy and sequential: Grounding DINO and SAM unload before SDXL;
SDXL unloads before 4-bit Qwen. Every unload nulls model references, runs Python
garbage collection, clears the CUDA cache, and collects CUDA IPC handles where
supported. Missing CUDA fails before model weights load.

Python AI dependencies are pinned in `ai_core/pyproject.toml`. Colab's preinstalled
PyTorch is intentionally not replaced: the notebook records the actual PyTorch,
CUDA, GPU, and VRAM values in diagnostics and the artifact manifest.

The bundled Manrope font comes from `google/fonts` commit
`7ff85c87f93ea6cca5f41c69f2e4edcb90240f26`, under the SIL Open Font License.
Runtime preflight verifies SHA-256
`d0639be45d0af36e798172419d7bd173c4bd4f29e2b76cbb69db1d11bf8b0a40`.
