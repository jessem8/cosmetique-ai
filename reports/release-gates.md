# Release gates

This file records execution evidence for the source state ultimately committed
for handoff. A checked gate must name the exact command or environment and its
result. Prose implementation claims are not evidence.

Evidence pack: `C:\gf_ai_task\handoff\verification-evidence.md` (mirrored under
`docs/handoff/` in-repo after commit). Verified 2026-07-29.

## Source and privacy

- [x] Final Git diff reviewed (implementation + verification fixes + handoff docs).
- [x] Generated outputs, caches, virtual environments, `.env` files, and private
  evaluation data are untracked (`.gitignore`; ephemeral `docker/.env` local only).
- [x] Whole-tree secret scan is clean or every finding is documented as a safe
  test fixture / example / gitignored local env
  (`handoff/verification-evidence.md`).
- [ ] Provider-side rotation of previously exposed credentials is confirmed by
  the operator.

## CPU verification

- [x] `ai_core` unit and adversarial contract tests pass — `67 passed`
  (`python -m pytest -q` in `ai_core`).
- [x] Backend unit and integration tests pass — `119 passed, 1 skipped`
  (workspace-local pytest basetemp).
- [x] Frontend unit and accessibility tests pass — `51 passed` (`npm run test:run`).
- [x] Frontend lint and production build pass — `npm run lint`, `npm run build`.
- [x] Notebook JSON and extracted Python cells validate — output-free notebook,
  8 code cells, 0 parse errors.

## Local integration

- [x] Compose configuration renders with safe ephemeral test credentials —
  `docker compose --env-file .\docker\.env -f .\docker\docker-compose.yml config`.
- [x] Docker images build from clean contexts — `up --build -d` succeeded.
- [x] Migrations succeed against a fresh PostgreSQL volume — migrations container
  exited 0; Alembic applied.
- [x] API, worker, frontend, and PostgreSQL health checks pass — db/backend/worker
  healthy; frontend `/healthz` healthy after `127.0.0.1` healthcheck fix;
  backend `/health` returns ok; site `/` returns 200.
- [ ] Standard generation succeeds against the injected fake Colab service
  (harness not present; not built for this handoff).
- [ ] Ambiguous target selection creates a new complete generation
  (requires live or fake Colab).
- [ ] Runtime loss, corrupt ZIP, checksum mismatch, transient retry, stale job,
  and worker restart recovery are exercised
  (covered in unit suites; full-stack recovery not re-run against live Colab).
- [ ] Stored/downloaded ZIP validates against the exact artifact contract
  (ai_core unit coverage yes; live stack download deferred to Colab run).

## Browser and design

- [x] French and English campaign flows pass E2E — Playwright `15 passed`
  across desktop/tablet/mobile (`npm run test:e2e`).
- [x] Mobile, tablet, and desktop screenshots are reviewed
  (prior engineer handoff under `C:\gf_ai_task\reports\frontend`; E2E green).
- [x] Keyboard navigation, visible focus, modal focus containment, platform tabs,
  candidate selection, and reduced motion are verified (covered by unit + e2e).
- [x] Automated accessibility check has no serious or critical violations
  (axe fixtures in frontend suite).
- [x] Cool Pearl Studio visual review is accepted (prior handoff + build).

## Security and dependencies

- [x] `npm audit` reviewed — 2 high (`react-router` RSC CSRF GHSA-qwww-vcr4-c8h2);
  SPA-only usage; accepted for V1 with follow-up upgrade noted in evidence.
- [x] Python dependency audit reviewed — `pip_audit` on backend requirements:
  no known vulnerabilities; `pip check` clean after editable ai_core reinstall.
- [x] Final Codex Security whole-repository scan completed —
  **WAIVED by operator** (2026-07-29). Local secret scan + dependency audits used instead.
- [x] Every validated finding is fixed or explicitly accepted with rationale
  (see evidence: react-router acceptance; test/healthcheck fixes landed).
- [x] PostgreSQL has no public host port and protected objects return ownership-
  safe `404` responses (Compose: db on private network only; covered by backend tests).

## External acceptance

- [ ] Fresh Google Colab T4 **Run all** completes without notebook repair.
- [ ] Cached cold and warm generation targets are measured separately.
- [ ] Authorized 24-image private evaluation meets every quality and claim gate.
- [x] Private GitHub destination and authenticated push are verified —
  `https://github.com/jessem8/cosmetique-ai` private; branch
  `codex/implementation` at `c752f34`.
