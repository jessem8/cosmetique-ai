# AI and Colab execution guide

## Start a fresh Colab runtime

1. Open `notebook/pipeline_ia_cosmetique.ipynb` from the same Google Drive that
   contains `Stage_1_ouvrier`.
2. Select a T4 GPU runtime and choose **Run all**.
3. Approve the Drive mount. The notebook finds exactly one adjacent
   `ai_core/pyproject.toml`, installs its pinned service/GPU extras without
   replacing Colab's PyTorch, checks CUDA, package versions, model revisions, font
   integrity, and starts the authenticated local runtime.
4. The final service cell downloads checksum-pinned `cloudflared` 2026.7.2 and
   prints the temporary `https://…trycloudflare.com` URL, runtime ID, and bearer
   token once.
5. Put those three values in the backend server environment and restart only the
   worker:

```env
AI_SERVICE_URL=https://example.trycloudflare.com
AI_SERVICE_TOKEN=<notebook bearer token>
AI_RUNTIME_ID=<notebook runtime id>
```

The URL and token are server-side secrets. Do not put them in React, screenshots,
Git, chat, or browser storage.

## Runtime contract

All `/v1` routes require the bearer token:

- `GET /v1/health`
- `POST /v1/jobs` with one `request` JSON form field and one `image` file
- `GET /v1/jobs/{id}`
- `GET /v1/jobs/{id}/bundle` with `X-Expected-Runtime-ID`

Health is ready only when CUDA, pinned packages, the bundled font, and immutable
model policy pass. The runtime accepts one active job. It returns measured stages,
never timers or guessed percentages. A changed session is `AI_RUNTIME_LOST`; a
lost runtime never becomes a degraded successful campaign.

The POST envelope includes the immutable source MIME, SHA-256, EXIF-corrected
width/height, and canonical input snapshot hash. Colab recomputes all of them
before scheduling inference. Images are capped at 20 MiB, 40 million decoded
pixels by default, and 100 million pixels as an absolute configurable ceiling.

## Direct notebook check

Set `RUN_DIRECT_DEMO = True` in the notebook form before **Run all**. Colab asks
for one authorized product photo, submits it through the same authenticated API,
polls objective states, validates the returned bundle, and downloads
`cosmetique-ai-campaign.zip`. Default service mode does not pause for an upload.

## Recovery

Quick Tunnels are demonstration/session infrastructure: URLs change on restart,
support at most 200 in-flight requests, do not support SSE, and have no uptime
guarantee. If Colab disconnects:

1. Mark in-flight work `AI_RUNTIME_LOST`; do not retry it as success.
2. Start a fresh notebook runtime.
3. Replace the backend URL, token, and runtime ID.
4. Restart the worker and submit a new generation.

Do not restore ngrok, unauthenticated routes, database credentials in Colab,
Cloudinary credentials, Ollama, rembg, silent model fallbacks, training, or LoRA.
