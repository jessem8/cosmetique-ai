# Cosmetique AI operations

This runbook covers the V1 deployment: a private local Docker application using
an authenticated, session-based Google Colab GPU service. A Colab runtime and a
ngrok tunnel are temporary infrastructure; neither is treated as always-on
production hosting.

## Trust boundaries

- The browser talks only to the local same-origin `/api/v1` API.
- The backend and worker keep the Colab URL and bearer token server-side.
- Colab receives only the uploaded image bytes and the validated generation
  request. It never receives PostgreSQL, storage, or website JWT credentials.
- PostgreSQL is attached only to the internal Docker network and has no host
  port.
- Product originals and generated artifacts are private objects served through
  authenticated API routes.

## Prerequisites

- Docker Desktop with Docker Compose v2.
- A Google account with access to a Colab T4 runtime.
- Enough local disk for PostgreSQL and the persistent artifact volume.
- A modern browser.

## Start the Colab service

1. Open the production notebook in `notebook/` with Google Colab.
2. Select a T4 GPU runtime.
3. Set a fresh high-entropy API bearer token in the notebook's protected
   configuration. Do not put the token in a committed cell.
4. Choose **Runtime → Run all**.
5. Wait for the authenticated health check to report `ready: true`.
6. Copy the generated `https://*.ngrok-free.app` URL and the bearer token
   into the local `docker/.env`.

The notebook launches the tunnel with the documented ngrok tunnel form:

```text
cloudflared tunnel --url http://localhost:<service-port>
```

ngrok tunnels use a random URL, have no uptime guarantee, do not support
server-sent events, and may return `429` after 200 concurrent in-flight requests.
Cosmetique AI uses bounded polling and one GPU job at a time.

## Configure the local stack

Copy the safe template:

```powershell
Copy-Item .\docker\.env.example .\docker\.env
```

Replace every `replace_...` value. Generate each secret independently; never
reuse the website JWT key, database password, or Colab bearer token.

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

`DATABASE_URL` must contain the same database name, user, and password as
`DB_NAME`, `DB_USER`, and `DB_PASSWORD`. Percent-encode an arbitrary password
before placing it in a URL, or use the URL-safe generator above.

## Start and verify Docker

From the repository root:

```powershell
docker compose --env-file .\docker\.env -f .\docker\docker-compose.yml config
docker compose --env-file .\docker\.env -f .\docker\docker-compose.yml up --build -d
docker compose --env-file .\docker\.env -f .\docker\docker-compose.yml ps
```

Open `http://localhost:<APP_PORT>`. The first startup applies Alembic migrations
before the API and worker start. The frontend starts only after the backend is
healthy.

Inspect sanitized service logs when troubleshooting:

```powershell
docker compose --env-file .\docker\.env -f .\docker\docker-compose.yml logs --tail 200 backend worker
```

Never paste full environment output or authorization headers into tickets or
chat.

## Colab runtime recovery

A stopped or replaced Colab runtime is expected to fail jobs explicitly with
`AI_RUNTIME_LOST` or `AI_SERVICE_UNAVAILABLE`; it must never produce a degraded
successful campaign.

1. Start a new T4 runtime and run the notebook from the top.
2. Create a new bearer token.
3. Wait for the new runtime health response and note its new runtime ID.
4. Update only `COLAB_AI_URL` and `AI_SERVICE_TOKEN` in `docker/.env`.
5. Recreate the backend and worker:

   ```powershell
   docker compose --env-file .\docker\.env -f .\docker\docker-compose.yml up -d --force-recreate backend worker
   ```

6. Create a new campaign. A terminal failed generation remains immutable.

The worker recovers expired processing leases after a restart. It does not
duplicate a bundle already stored and validated.

## Stop and preserve data

```powershell
docker compose --env-file .\docker\.env -f .\docker\docker-compose.yml down
```

This preserves the named PostgreSQL and artifact volumes. Removing volumes
deletes local application data and is intentionally not part of the routine
shutdown command.

## Private evaluation data

Keep authorized originals, annotations, and expert masks outside Git. The
recommended ignored in-repository mount point is `data/evaluation/private/`; a
separate access-controlled directory may be used instead. See
`docs/EVALUATION.md`.

## Failure interpretation

| Code | Meaning | Operator action |
| --- | --- | --- |
| `AI_SERVICE_UNAVAILABLE` | Colab health preflight failed | Start/reconnect Colab, then retry as a new generation |
| `AI_RUNTIME_LOST` | Runtime identity changed during a job | Reconnect the new runtime; do not reuse the old job |
| `TARGET_AMBIGUOUS` | More than one plausible product | Select a displayed candidate; create a new generation |
| `CLAIM_SAFETY_FAILED` | Copy was not supported by verified facts | Correct the brief; do not blindly retry |
| `ARTIFACT_CONTRACT_FAILED` | The returned ZIP is malformed or inconsistent | Inspect sanitized worker logs and AI diagnostics |
| `GENERATION_STALE` | The bounded remote deadline expired | Check the Colab runtime and create a new generation |
