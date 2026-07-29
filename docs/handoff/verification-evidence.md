# Cosmetique AI — verification evidence

Date: 2026-07-29  
Workspace: `C:\gf_ai_task\fully_cloned\Stage_1_ouvrier`  
Branch: `codex/implementation`  
Operator: Cursor agent (handoff finish)

Claims below name the exact command and result. Unchecked external gates are not inferred as done.

## CPU / unit layers

| Gate | Command | Result |
|---|---|---|
| ai_core | `ai_core\.venv\Scripts\python.exe -m pytest -q` | **67 passed** |
| backend | `backend\.venv\Scripts\python.exe -m pytest -q --basetemp C:\gf_ai_task\.test_tmp_backend_handoff\pytest` with `TMP`/`TEMP` under workspace | **119 passed, 1 skipped** |
| frontend lint | `npm run lint` | **exit 0** |
| frontend unit | `npm run test:run` | **51 passed** (12 files) |
| frontend build | `npm run build` | **exit 0** |
| frontend e2e | `npm run test:e2e` | **15 passed** (desktop/tablet/mobile) |
| notebook | JSON load + AST parse of non-magic code cells | **ok** — 10 cells, 8 code, 0 saved outputs, 0 parse errors |

Fixes applied during this verification:

- `ai_core/tests/test_orchestrator.py` — pass `background_validator` stub; assert validate/unload events.
- `ai_core/tests/test_service.py` — busy job uses a distinct `request_id` (idempotent replay is not 429).
- `docker/docker-compose.yml` — frontend healthcheck uses `127.0.0.1` instead of `localhost` (IPv6 refuse).

## Hugging Face model pins

Tool: Hugging Face MCP `hub_repo_details` (user `jessem66`).

| Repo | License (Hub overview) | Pinned revision in `model_registry.py` |
|---|---|---|
| IDEA-Research/grounding-dino-tiny | apache-2.0 | `a2bb814dd30d776dcf7e30523b00659f4f141c71` |
| facebook/sam-vit-base | apache-2.0 | `70c1a07f894ebb5b307fd9eaaee97b9dfc16068f` |
| stabilityai/stable-diffusion-xl-base-1.0 | openrail++ | `462165984030d82259a11f4367a4eed129e94a7b` |
| Qwen/Qwen2.5-7B-Instruct | apache-2.0 | `a09a35458c702b33eeacc393d103063234e8bc28` |

Hub overview confirmed for all four. No weights downloaded in this run.

## Docker smoke

Ephemeral `docker/.env` generated locally (gitignored). Placeholder Colab URL used.

| Step | Result |
|---|---|
| `docker compose ... config` | exit 0 |
| `docker compose ... up --build -d` | exit 0 |
| db | healthy |
| migrations | exited 0 |
| backend | healthy — `GET /health` → `{"status":"ok","version":"1.0.0"}` |
| worker | healthy |
| frontend | healthy after healthcheck fix — `GET /healthz` → `ok`; `GET /` → 200 |
| Real Colab generation | **not executed** (placeholder tunnel) |

## Dependencies

| Check | Result |
|---|---|
| `pip_audit -r backend/requirements.txt` | **No known vulnerabilities found** |
| `pip check` (backend venv after reinstalling editable ai_core) | **No broken requirements found** |
| `npm audit --omit=dev` | **2 high** — `react-router` / `react-router-dom` 7.18.x (GHSA-qwww-vcr4-c8h2, RSC Mode CSRF). App uses client SPA routing only (no RSC). Accepted for V1 handoff; upgrade tracked as follow-up. |

## Secret scan

Python walk of source tree excluding `.venv`, `node_modules`, `.git`, `dist`.

Hits limited to:

- `docker/.env` (local ephemeral, gitignored)
- `*.env.example` placeholders
- `backend/tests/conftest.py` test DSN fixture

No unexpected live credentials in tracked source.

## Explicitly not claimed

- Fresh Google Colab T4 **Run all**
- Live Quick Tunnel end-to-end generation
- Authorized 24-image private evaluation
- Provider-side rotation of historically exposed credentials
- Codex Security whole-repo scan (**waived by operator**)
- Fake-Colab Compose integration harness (does not exist; not built)
