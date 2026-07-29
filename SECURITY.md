# Security policy and privacy operations

## Required secret handling

- Keep `.env`, Colab tokens, model access tokens, database credentials, and
  private evaluation data outside Git.
- Generate a distinct high-entropy value for the website JWT key, PostgreSQL
  password, and Colab API bearer token.
- Rotate the Colab token whenever a runtime is replaced.
- Rotate any credential that was ever exposed in source or chat at its provider;
  removing it from the current tree is not sufficient.
- Purge sensitive values from upstream Git history before any repository is
  shared.

Safe templates are committed only as `.env.example` files. Do not add realistic
placeholder secrets that could be mistaken for working defaults.

## Application controls

- All user resources are ownership-scoped; missing and non-owned objects both
  return `404`.
- Uploads are streamed with byte and decoded-pixel limits, magic-byte validation,
  EXIF correction, bounded dimensions, and private storage.
- Generation creation is idempotent and hashes the immutable input snapshot.
- Colab health, job, status, and bundle endpoints all require bearer
  authentication.
- Remote errors, paths, tokens, environment values, and tracebacks are never
  returned to the browser.
- Bundles are treated as untrusted input and validated before storage.
- PostgreSQL has no public host port.
- Nginx terminates the browser boundary and adds conservative response headers.

## Release checks

Before a private handoff:

1. Run backend, AI-core, frontend unit, integration, accessibility, and E2E tests.
2. Build the frontend and Docker images from a clean context.
3. Apply migrations to a fresh PostgreSQL volume.
4. Run Python and npm dependency audits; triage every finding rather than forcing
   incompatible upgrades.
5. Run a whole-tree secret scan including notebook cell source and Git diff.
6. Run the application-security review against the final stable source.
7. Confirm `.env`, private data, artifacts, caches, reports with sensitive
   payloads, and generated bundles are untracked.
8. Confirm the destination GitHub repository is private before pushing.

## Operator-owned gates

Provider-side credential rotation, upstream-history cleanup, private-dataset
rights, the real fresh-T4 Colab run, and GitHub authentication require the
operator. These gates are reported as unexecuted until direct evidence exists.
