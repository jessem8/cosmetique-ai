# Frontend Baseline Audit

**Project:** Cosmetique AI  
**Canonical source:** `C:\gf_ai_task\fully_cloned\Stage_1_ouvrier`  
**Audit date:** 2026-07-28  
**Comparison target:** the locked product, architecture, acceptance gates, and
Cool Pearl Studio direction in `C:\gf_ai_task\PROJECT_MEMORY.md`

## Scope and constraints

This is a source-level, read-only baseline audit. It covers the React routes,
API client, authentication forms, campaign creation flow, generation status
screen, result screen, history/dashboard, shared components, global CSS,
accessibility, and frontend build/test setup.

No `.env` file, private backup, private dataset, credential, generated artifact,
or production dependency tree was inspected. No application source was changed.
The existing build was not executed because this audit was authorized to write
only this report and a normal Vite build emits `dist/`.

## Executive finding

The frontend is a small, understandable React/Vite application and is a viable
base for incremental improvement. It is not yet a truthful or contract-safe V1.
The largest risks are behavioral rather than cosmetic:

1. `Generation.jsx` fabricates a 90-second countdown and advances pipeline steps
   on a timer unrelated to the server.
2. `Dashboard.jsx` is not generation history. It performs an N+1 request pattern
   and keeps only the newest generation per product.
3. `Result.jsx` treats any nonempty asset array as a downloadable success, uses
   legacy partial-regeneration endpoints, and has no manifest/evidence contract.
4. `Upload.jsx` cannot collect campaign language or the verified facts required
   for claim-safe copy, and a retry can create duplicate/orphaned products.
5. The current API paths and payloads do not match the locked `/api/v1`
   architecture, lack creation idempotency, and provide no ambiguity-recovery
   seam.
6. The current warm rose/gold/glass/emoji visual system is materially different
   from the locked Cool Pearl Studio identity and has significant keyboard,
   motion, dialog, live-region, and contrast defects.
7. There is no frontend test, lint, accessibility, or end-to-end harness.

The safest implementation order is to lock the browser-facing generation
contract and cover it with tests before touching the visual system. A visual
overhaul performed first would preserve the false states underneath a polished
surface.

## Current frontend inventory

| Area | Current file(s) | Baseline |
|---|---|---|
| Runtime | `package.json`, `vite.config.js`, `src/main.jsx` | React 18.3.1, React Router 6.26.1, Axios 1.7.7, Vite 5.4.8, JavaScript |
| Routes/shell | `src/App.jsx`, `src/components/Navbar.jsx` | Public login/register; token-presence protected dashboard/new/generation/result routes |
| API | `src/api/client.js` | One Axios instance; legacy unversioned paths; bearer token from local storage |
| Authentication | `src/pages/Login.jsx`, `src/pages/Register.jsx` | Local form state, raw API errors, auto-login after registration |
| Campaign creation | `src/pages/Upload.jsx` | Three local steps: image, limited product metadata, confirmation |
| Generation state | `src/pages/Generation.jsx`, `src/components/StepLoader.jsx` | Polling plus simulated countdown and simulated pipeline |
| History | `src/pages/Dashboard.jsx` | Product grid enriched with only the latest generation |
| Result | `src/pages/Result.jsx`, `PosterPreview.jsx`, `AssetCard.jsx` | Three poster previews, generic copy, legacy partial regeneration, ZIP download |
| Styling | `src/index.css` plus many inline styles | One 44 KB global stylesheet; warm rose/glass visual language |
| Production build | `docker/Dockerfile.frontend`, `docker/nginx.conf` | Node 20 `npm ci`/Vite build served by nginx with SPA fallback |
| Automated checks | `package.json` | `dev`, `build`, `preview` only; no frontend tests or static checks |

## Severity model

- **P0 — truth/integrity:** can misrepresent server work, produce duplicates,
  expose incomplete output as success, or contradict a locked product guarantee.
- **P1 — workflow/accessibility:** blocks a required V1 path or materially harms
  keyboard, screen-reader, mobile, or recovery behavior.
- **P2 — quality/maintainability:** weakens visual quality, clarity, performance,
  or future change safety without independently corrupting the outcome.

## Truthful state model

The UI needs to distinguish the durable job state from the browser's ability to
reach it. The minimum frontend state model is:

| Server/browser state | UI behavior |
|---|---|
| Initial request loading | Neutral skeleton/status; no guessed stage |
| `pending` | “En attente”; no percentage or ETA |
| `processing` + real `stage` | Highlight the server-reported stage only |
| `error` + `TARGET_AMBIGUOUS` | Show candidate boxes and let the user start a new generation with a selected normalized `target_hint` |
| `error` + infrastructure code | Explain whether the AI service/runtime is unavailable and offer a safe retry/new generation action |
| `error` + terminal validation/AI code | Show the exact user-safe reason; never label an output successful |
| `done` | Enable result viewing only after the backend has validated the strict artifact contract |
| Poll transport failure | Preserve the last known server state, show a reconnecting banner, retry with bounded backoff |

`status` and `stage` are separate. The frontend must never derive a stage from
elapsed time. It may render the ordered locked stage list—analysis, extraction,
art direction, background, composition, copy, export, packaging—but completion
comes only from the current server stage and terminal status. No percentage is
necessary for V1.

## Findings by file and seam

### `frontend/src/App.jsx`

**P1**

- `ProtectedRoute` checks only whether a local-storage string exists. An expired
  or the literal string `"undefined"` is treated as authenticated until the
  first API request fails.
- Login receives `state.from`, but `Login.jsx` ignores it and always navigates to
  `/dashboard`; a user whose session expires loses the requested destination.
- The protected fragment has a `<nav>` but no `<main>` landmark or skip link.
- The wildcard route silently redirects every bad URL to the dashboard. This
  hides broken links and gives no not-found state.
- There is no route-level error boundary. A render exception blanks the app.

**P2**

- Authenticated users can revisit login/register without redirection.
- Page titles do not change by route.
- Each protected route repeats the shell wrapper.

**Smallest seam**

- Keep React Router and the current route URLs.
- Add one `AppShell` with a skip link, `Navbar`, and `<main id="main-content">`.
- Add a small `RequireAuth` wrapper that rejects missing/invalid token values and
  preserves the requested location.
- Add a route error fallback and explicit French not-found page.
- Defer route merging; it is not required to deliver the locked V1.

### `frontend/src/api/client.js`

**P0**

- The default base URL is `http://localhost:8000` and every endpoint is
  unversioned. The locked browser API is `/api/v1`.
- Generation creation is `POST /generations/{productId}` rather than
  `POST /api/v1/products/{product_id}/generations`.
- Creation sends no `Idempotency-Key`. A double click, React retry, or manual
  retry can create duplicate generations.
- `regenerateText` and `regenerateDecor` encode partial mutation behavior that
  contradicts the locked “complete new generation, optional
  `source_generation_id`, no partial revision graph” decision.
- ZIP download uses `/download-zip` rather than the locked `/bundle`.
- History is exposed only as `/products/{id}/generations`; the locked cursor API
  is `GET /api/v1/generations?cursor=&limit=`.

**P1**

- There is no method for authenticated per-file artifact retrieval, candidate
  ambiguity recovery, or a new generation based on a previous generation.
- Error extraction is duplicated in every page and does not preserve stable
  codes such as `TARGET_AMBIGUOUS`, `AI_SERVICE_UNAVAILABLE`, or
  `AI_RUNTIME_LOST`.
- A single 401 performs a global `window.location.href` change, drops the
  requested route, and cannot be coordinated with React navigation.
- The 30-second instance timeout is also applied to bundle downloads. A valid
  local ZIP may exceed it.
- Requests do not accept `AbortSignal`; stale responses can update a page after
  route or parameter changes.
- `downloadZipUrl` cannot attach the bearer header and would be unsuitable for a
  protected bundle/artifact.

**Smallest seam**

Keep Axios; no data-fetching framework is required. Replace the legacy surface
with these methods:

```text
auth.login(credentials)
auth.register(credentials)
products.create(formData, { signal })
generations.create(productId, payload, { idempotencyKey, signal })
generations.get(id, { signal })
generations.list({ cursor, limit, signal })
generations.getArtifact(id, filename, { signal, responseType: "blob" })
generations.getBundle(id, { signal, responseType: "blob", timeout })
```

Every rejected request should become one normalized `ApiError` containing
`status`, `code`, `message`, `retryable`, and the original request context. The
pages should render by `code`, not parse arbitrary `detail` strings.

### `frontend/src/pages/Login.jsx`

**P1**

- `noValidate` disables native validation, but the replacement validation checks
  only presence; malformed email addresses are submitted.
- Error content has no `role="alert"` or live region and is not associated with
  the relevant inputs.
- The password visibility button is removed from keyboard order with
  `tabIndex={-1}` and has no accessible name or pressed state.
- Successful login ignores the protected destination stored by `RequireAuth`.

**P0 content integrity**

- “premium en secondes” and “Génération IA ultra-réaliste” are unsupported
  outcome/speed claims and conflict with the locked cold/warm generation gates.

**Smallest seam**

Retain the page and local state. Use native `required`/email semantics, add
field-level `aria-invalid`/`aria-describedby`, make the password control a named
keyboard button, announce errors, and restore the requested destination. Replace
claims with factual descriptions of the three exact formats and verified-input
copy.

### `frontend/src/pages/Register.jsx`

**P0**

- Registration does not verify that auto-login returned a token before storing
  it. `localStorage.setItem(key, undefined)` stores a truthy `"undefined"` value,
  which `ProtectedRoute` accepts.
- The page claims a free trial, results in under 90 seconds, guaranteed
  professional quality, instant downloads, and thousands of professionals.
  None is supported by the locked product or current implementation.

**P1**

- The password UI presents uppercase and digit requirements, but submit enforces
  only eight characters. The displayed policy and actual policy diverge.
- The same password-control and error-announcement defects as login apply.
- Registration and login are two mutations with no explicit partial-success
  recovery if registration succeeds but auto-login fails.

**Smallest seam**

Retain auto-login only if the backend contract supports it, validate the returned
token, and display a safe “account created; please sign in” fallback. Share the
accessible auth-field primitives with login without introducing a UI library.

### `frontend/src/components/Navbar.jsx`

**P1**

- The mobile trigger lacks `aria-expanded` and `aria-controls`; its label does
  not say whether it opens or closes the menu.
- The mobile menu has no Escape handling, focus management, outside-click
  behavior, or focus restoration.
- “Dashboard” is English inside a locked French interface.
- Global CSS provides no visible keyboard focus for these links/buttons.
- At narrow widths, the full email, logout control, and hamburger remain in the
  same 24-pixel-padded row and can overflow.

**P2**

- Much of the mobile menu styling is inline and hard-codes the obsolete warm
  palette.
- Emoji/symbol controls do not match the refined CA identity.
- The full email is visually prominent in every authenticated view despite not
  being primary workflow information.

**Smallest seam**

Keep a top navigation for V1. Add the CA monogram, French labels, a semantic
disclosure button, a controlled mobile panel, and one compact account menu. Use
inline SVG icons from one audited icon set or a tiny local icon module; do not
add an animation framework.

### `frontend/src/pages/Dashboard.jsx`

**P0**

- This is not history. It loads products and then performs one generation
  request per product, retaining only `genList[0]`. Older generations and full
  regenerations are invisible.
- A per-product generation request failure is silently converted to “no
  generation.” Clicking that card opens `/new`, which can lead to duplicate
  products/work.
- The displayed count is a product count but is labelled “Mes Créations.”
- The category code is `hygiene_deo`, while upload/result use `hygiene`.

**P1**

- Failed, ambiguous, pending, and processing generations cannot reliably be
  resumed from one authoritative list.
- The card is a `div role="button"` that implements Enter but not Space. A link
  is the correct primitive.
- Unknown statuses default to “En attente,” falsely converting contract drift
  into a normal queue state.
- There is no cursor pagination, so the required global history endpoint has no
  frontend seam.
- Skeletons have no busy/status semantics.

**P2**

- Thumbnail `object-fit: cover` can crop the generated composition.
- Per-card stagger animations ignore reduced-motion preferences.
- Search, filters, and dashboard analytics are not necessary for V1; do not add
  them during the correction.

**Smallest seam**

Replace the enrichment logic with one cursor request to
`GET /api/v1/generations?cursor=&limit=`. Render one semantic link/card per
generation, including product, language, status, real stage or stable error,
timestamp, and a noncropped preview when available. Route `pending`,
`processing`, and every `error` to `/generations/:id`; route `done` to
`/result/:id`. Add only “load more,” not search/filter complexity.

### `frontend/src/pages/Upload.jsx`

**P0**

- Required claim-safety inputs are absent. The form collects only name, brand,
  category, tone, and template; it cannot supply output language, audience,
  verified benefits, ingredients, verified claims, or CTA.
- Campaign language is absent, so the UI cannot guarantee exactly one French or
  English campaign per generation.
- Product creation and generation creation are separate calls with no retained
  `productId` and no idempotency. If product creation succeeds and generation
  creation fails, pressing launch again creates another product.
- The confirmation states “60 à 90 secondes,” contradicting the locked cold/warm
  acceptance targets.
- Category/template codes already drift (`hygiene` versus `hygiene_deo`,
  `classique` versus the likely canonical API enum), with constants duplicated
  across pages.

**P1**

- The dropzone responds to Enter but not Space, has no associated visible file
  input label, and does not announce validation errors.
- Template radios are `display: none`, so keyboard users cannot select them.
- Inputs do not expose required/error state with `aria-required`,
  `aria-invalid`, or `aria-describedby`.
- The step indicator is visual `div` content rather than an ordered process with
  a current-step announcement.
- File checks trust browser MIME and byte size only; there is no image decode
  error, dimension feedback, or same-file reselection reset.
- Smooth forced scrolling has no reduced-motion guard.
- Submission has no `aria-busy`/status announcement.

**Smallest seam**

Keep the three-step flow to limit churn:

1. **Photo:** accessible file input/dropzone, decoded preview, supported format
   and size feedback.
2. **Brief vérifié:** name and category required; brand, audience, benefits,
   ingredients, verified claims, and CTA optional; exactly one campaign language
   (`fr` or `en`); one canonical art-direction enum.
3. **Review:** show the exact input snapshot, explain that duration varies, and
   create the generation once with an `Idempotency-Key`.

After `products.create` succeeds, retain the returned `productId` and retry only
generation creation. Centralize category, language, stage, and art-direction
codes in one domain constants file shared by upload, history, generation, and
result views.

### `frontend/src/pages/Generation.jsx`

**P0 fake state**

- `timeLeft` always starts at 90 and decrements locally. It has no server basis.
- `currentStepIdx` advances every 12 seconds regardless of `status` or backend
  work.
- A pending job immediately appears to be performing “Détourage.”
- `STATUS_STEP_MAP` is declared but unused.
- The hard-coded status has only four coarse values and ignores the locked real
  stage.
- A single transient polling failure stops polling permanently and is rendered
  as “Génération échouée,” even if the durable job continues successfully.
- `setInterval` can issue overlapping polls when a response takes longer than
  2.5 seconds.

**P1**

- `TARGET_AMBIGUOUS` has no candidate-box view or selected `target_hint` action.
- Stable AI service/runtime failures have no specific explanation or safe
  recovery.
- The error path stops the poll timer but leaves the fake stage-cycle timer
  running until unmount.
- The delayed success navigation is not cleaned up and moves users
  automatically without an explicit result action.
- No status change is announced with `aria-live`.
- Refresh/reconnect behavior is not modeled separately from terminal job error.

**Smallest seam**

Extract one `useGenerationPolling(id)` hook that:

- performs one request at a time;
- accepts an abort signal;
- uses recursive timeout with bounded backoff and resets after success;
- preserves the last known job during a transport outage;
- stops only on `done`, terminal `error`, or unmount;
- exposes `{generation, initialLoading, transportError, refresh}`.

Render a pure `GenerationStatusView` from the server model. Remove all local ETA,
percent, and stage-cycle state. For `TARGET_AMBIGUOUS`, display the
EXIF-corrected source image with normalized candidate overlays and an adjacent
keyboard-selectable candidate list. Selection starts a complete new generation
using the chosen box; no mask brush/editor is needed.

### `frontend/src/components/StepLoader.jsx`

**P0**

- The six steps are not the locked pipeline. They omit analysis, art direction,
  and packaging; they include “Upload des assets,” which reflects the obsolete
  Cloudinary design.
- Progress percentage is inferred from the active index and therefore appears
  authoritative despite being synthetic.
- The first active step displays 0%, and the final active step displays 100%
  before it has completed.

**P1**

- The step list has no list/progress semantics, current-step announcement, or
  non-color state text suitable for assistive technology.
- Infinite spin/pulse animation ignores reduced motion.

**Smallest seam**

Replace it with `GenerationTimeline`. Feed it the canonical ordered stage
constant and the server-reported current stage. Use text states (“Terminé,” “En
cours,” “À venir”) with an ordered list and `aria-current="step"`. Do not expose
a percent.

### `frontend/src/pages/Result.jsx`

**P0**

- The page fetches generation and assets separately. `Promise.all` makes the
  entire result fail if either request fails and has no single validated
  manifest source of truth.
- It always displays a “✓ Généré” badge after loading; it does not verify
  `status === "done"` or the strict artifact contract.
- “Télécharger tout” is enabled when `assets.length > 0`, not when the immutable
  validated bundle is ready. One poster can be treated as complete success.
- Copy is one generic title/subtitle/bullet/CTA object rather than a structured
  platform-specific payload.
- Text regeneration mutates the current result in component state through a
  legacy partial endpoint.
- Decor regeneration can fall back to the same generation ID and therefore
  permits in-place mutation. Both behaviors contradict immutable complete
  regeneration.
- There is no evidence view for `cutout.png`, `mask.png`, or `background.jpg`,
  and no manifest/package contents view.

**P1**

- Format “tabs” only change a border; all three posters remain visible. They have
  no `role="tablist"`, tab state, or tab panels.
- Poster previews and export cards duplicate the same assets.
- Native `alert()` is used for decor and download failure.
- The confirmation modal has no dialog role, accessible title/description,
  initial focus, focus trap, Escape behavior, or focus restoration.
- The modal repeats the false 60–90-second promise.
- The selected campaign language is not shown; English copy cannot be marked
  with `lang="en"`.
- There is no copy-to-clipboard action for the actual platform copy.
- A completed generation reached directly with a malformed/incomplete response
  degrades silently into empty placeholders.

**Smallest seam**

Use one result workspace:

- one large, noncropped poster viewer;
- three semantic platform tabs/thumbnails;
- the selected platform's structured copy with a Copy action;
- a compact evidence disclosure for original/cutout/mask/background;
- package readiness and the exact eight-file manifest;
- one “Créer une nouvelle variation” action that creates a complete new
  generation with `source_generation_id`;
- one authenticated bundle download.

Remove the duplicate asset-card grid and both partial-regeneration controls.
Keep generated copy read-only in V1; persistent free-form editing would require a
new claim-safety and persistence contract.

### `frontend/src/components/PosterPreview.jsx`

**P0**

- `.poster-preview-img { object-fit: cover; }` can crop poster edges, text, and
  safe zones, so the preview can misrepresent a correctly exported artifact.
- Facebook and LinkedIn share an approximate `191 / 100` CSS ratio despite their
  distinct exact dimensions.

**P1**

- Direct cross-origin anchors with `download` may open rather than download and
  cannot attach the bearer token required by local authenticated artifact
  storage.
- Download controls are hidden until pointer hover and have no equivalent
  focus-visible rule.
- The English alt text is generic.
- Image-loaded/error state is not reset if `url` changes.
- The card has a pointer cursor even though the card itself has no action.

**Smallest seam**

Render the exact width/height ratio from manifest metadata, use `object-fit:
contain`, fetch protected artifacts through the API client into revocable object
URLs, reset load state when the source changes, and keep a permanently available
named download button.

### `frontend/src/components/AssetCard.jsx`

**P1**

- The copy-URL action silently discards clipboard failures and has no live
  status.
- Copying private/local artifact URLs is not a useful V1 action and may expose an
  ephemeral location.
- Direct downloads have the same bearer/cross-origin problem as
  `PosterPreview`.
- Thumbnail `object-fit: cover` crops artifacts.

**P2**

- `.copy-btn` is never styled in `index.css`, so it falls back to browser
  defaults.
- The component duplicates poster display and download controls already shown
  above it.
- A copied-state timeout is not cleaned up.

**Smallest seam**

Remove this duplicate component from the result path. If a manifest file list is
needed, replace it with a compact semantic `ManifestFileRow` showing filename,
dimensions/type, size, validation state, and one authenticated download action.

### `frontend/src/index.css`

**P1 accessibility**

- There is no global `:focus-visible` system for links, buttons, custom controls,
  tabs, or cards.
- There is no `prefers-reduced-motion` override despite continuous float, spin,
  pulse, shimmer, orb, step-pulse, shake, scale, and entry animations plus smooth
  scrolling.
- Several interactions are revealed only by hover.
- Approximate contrast failures include white text on `--rose: #F4839B`
  (roughly 2.45:1), `--text-muted: #B89CA6` on white (roughly 2.5:1), and
  `--gold: #C98B6A` on white (roughly 2.8:1). These fail WCAG AA for normal text;
  white on rose also fails the 3:1 large-text threshold.
- Native input outlines are removed. Inputs receive a custom focus ring, but
  buttons, links, radio-card labels, card links, and mobile controls do not.
- Responsive rules stop at 640/900 pixels; the fixed navbar remains crowded at
  360–390 pixels.

**P2 Cool Pearl Studio mismatch**

| Locked direction | Current baseline |
|---|---|
| Pearl-white surfaces | Warm off-white/beige base with pink radial background |
| Graphite typography | Dark plum and rose-gray text |
| Deep raspberry accent | Light baby pink plus gold and lavender accents |
| Instrument Serif display | Outfit |
| Manrope UI | Inter |
| Refined CA monogram | Gradient text name and emoji/symbol marks |
| Solid editorial surfaces | Repeated glass blur, translucent cards, glows |
| Restrained purposeful motion | Many continuous decorative animations |
| Precise studio tool | Emojis, gradient text/buttons, orbs, pill-heavy styling |

Additional maintainability defects:

- Google fonts are requested twice: a `<link>` in `index.html` and an `@import`
  in CSS.
- The 44 KB global file is coupled to many inline declarations in JSX.
- `.inline-spinner` is used but not defined; the secondary regeneration button's
  `.btn-loading` spinner is white on a white surface.
- `.copy-btn` is used but not defined.
- Several unused generic styles (`env-banner`, tooltip helpers, some glass
  variants) add noise.
- Noninteractive glass cards receive hover treatment, implying action.

**Smallest seam**

After behavioral tests pass, establish a thin vanilla-CSS system:

```text
src/styles/tokens.css      Cool Pearl colors, type, spacing, radii, elevation
src/styles/base.css        reset, typography, focus, reduced motion, landmarks
src/styles/components.css  buttons, fields, badges, dialog, tabs, timeline
src/styles/pages.css       page composition and responsive rules
```

Self-host pinned Instrument Serif and Manrope webfonts for reproducible local
Docker/Colab demos. Use one deep raspberry accent, graphite text, pearl/white
surfaces, hairline neutral borders, 10-pixel controls, approximately 16-pixel
panels, and minimal elevation. Replace emoji controls with coherent line icons.
Keep motion to short state transitions and respect reduced motion.

### `frontend/index.html`

**P2**

- The font link duplicates the CSS import and uses the obsolete typefaces.
- There is no application icon/CA monogram metadata.
- The description says “affiches premium IA” without explaining verified-input
  copy or exact outputs.

**Smallest seam**

Use locally bundled font declarations, add the CA favicon/metadata, retain
`lang="fr"`, and keep claims factual. Set `lang="en"` only around English
campaign copy content.

### `frontend/src/main.jsx`

The entry point is intentionally small and can remain. React Strict Mode is
valuable and should not be removed to hide duplicate-effect defects. Add test
providers only in the test harness; a production state library is unnecessary.

### Build and serving files

#### `frontend/package.json`

- The dependency surface is appropriately small.
- Scripts are only `dev`, `build`, and `preview`.
- There is no `test`, `test:watch`, `test:coverage`, `lint`, `e2e`, or combined
  `check`.
- There is no React Testing Library, Vitest, jsdom, MSW, user-event, axe,
  Playwright, ESLint, or JSX accessibility plugin.

#### `frontend/vite.config.js`

- Only the React plugin and port are configured.
- There is no test environment/setup, coverage policy, development API proxy, or
  deterministic preview host configuration.

#### `docker/Dockerfile.frontend`

- The two-stage Node 20/nginx build and `npm ci` are sound foundations.
- The comment says the API URL can be overridden “at runtime,” but Vite embeds
  `VITE_API_URL` at build time. Changing the nginx container environment will not
  change the built client.
- A same-origin `/api/v1` proxy would remove build-time origin coupling, but this
  must be coordinated with the backend/Docker contract.

#### `docker/nginx.conf`

- SPA fallback and hashed static-asset caching are useful.
- There is no `/api/v1` reverse proxy, so a relative versioned API base currently
  falls into the SPA handler.
- Static media are cached immutable for a year. Generated authenticated artifacts
  must not be served through that static rule.

#### Existing check status

No frontend test files or frontend test configuration were found. The only
repository test-like file is AI-side (`ai/scripts/test_pipeline.py`). The
frontend production build was not run during this report-only audit, so this
report does not claim that the current code compiles or runs.

## Locked API seams the frontend needs

The four required endpoints in project memory are necessary but their response
shapes do not yet fully support the locked frontend. The smallest browser-facing
contract should cover the following.

### Create generation

```http
POST /api/v1/products/{product_id}/generations
Idempotency-Key: <uuid>
Content-Type: application/json
```

Minimum request:

```json
{
  "language": "fr",
  "audience": "string or null",
  "benefits": ["verified input only"],
  "ingredients": ["verified input only"],
  "verified_claims": ["verified input only"],
  "cta": "string or null",
  "art_direction": "canonical enum",
  "target_hint": null,
  "source_generation_id": null
}
```

Exactly one `language` value is sent. An ambiguity recovery or variation is a
new complete generation. It supplies `source_generation_id`; ambiguity recovery
also supplies the selected normalized box as `target_hint`.

### Get generation

The frontend minimally needs:

```json
{
  "id": "uuid",
  "product": {
    "id": "uuid",
    "name": "string",
    "brand": "string or null",
    "category": "canonical enum"
  },
  "status": "pending | processing | done | error",
  "stage": "analysis | extraction | art_direction | background | composition | copy | export | packaging | null",
  "language": "fr | en",
  "created_at": "ISO-8601",
  "updated_at": "ISO-8601",
  "source_generation_id": "uuid or null",
  "input_snapshot": {},
  "error": null,
  "copy": null,
  "manifest": null
}
```

For an error, `error` needs at least
`{code, message, retryable}`. `TARGET_AMBIGUOUS` additionally needs the
EXIF-corrected image dimensions/preview and candidate normalized boxes. For
`done`, `copy` must expose the parsed, platform-specific `copy.json` payload and
`manifest` must expose all eight validated file records.

### List generations

```json
{
  "items": [
    {
      "id": "uuid",
      "product": {},
      "status": "pending | processing | done | error",
      "stage": "canonical stage or null",
      "language": "fr | en",
      "created_at": "ISO-8601",
      "updated_at": "ISO-8601",
      "error": null,
      "preview": null
    }
  ],
  "next_cursor": "opaque string or null"
}
```

The list must contain every generation, not one row per product.

### Bundle and artifact preview

`GET /api/v1/generations/{id}/bundle` must stream only the already-built,
backend-validated immutable ZIP. The frontend must not reconstruct or partially
download it.

The locked endpoint list does not yet identify how the authenticated UI loads
individual posters and evidence files. Because `<img>` cannot attach the current
bearer header, one of these contracts must be chosen before result
implementation:

1. add an authenticated
   `GET /api/v1/generations/{id}/artifacts/{filename}` and let Axios fetch blobs
   into revocable object URLs; or
2. move web authentication to a same-site secure cookie and serve protected
   artifact URLs directly.

For the current bearer-token architecture, option 1 is the smallest change. Do
not use query-string tokens or public local-storage paths.

## Accessibility acceptance baseline

The redesign is not complete until all of the following are covered:

- Semantic landmarks, one page `<h1>`, skip link, and meaningful document title.
- Complete keyboard operation, including Space/Enter where a custom primitive
  remains, visible `:focus-visible`, and no hover-only action.
- Native labels, required state, input error associations, and announced async
  form errors.
- `aria-live` for generation state, reconnection, copy success, and download
  failure; no rapidly updating countdown.
- Semantic ordered generation timeline and semantic tabs/tab panels.
- Accessible modal/dialog behavior or a native `<dialog>` implementation with
  tested focus behavior.
- Candidate selection usable from the adjacent radio/list controls without
  requiring precise pointer interaction on the image.
- Text and non-text contrast meeting WCAG 2.2 AA.
- `prefers-reduced-motion` support and no required information encoded only by
  color or animation.
- Meaningful French alt text; decorative icons hidden from assistive technology.
- English generated copy wrapped with `lang="en"`.
- Responsive verification at 360, 390, 768, 1024, and 1440 CSS pixels.

## Minimal test harness

Add only the tooling needed to protect the locked workflow:

- **Vitest + jsdom** for unit/component tests.
- **React Testing Library + `@testing-library/user-event` +
  `@testing-library/jest-dom`** for behavior and keyboard interaction.
- **MSW** for browser-contract fixtures and state transitions. Fixtures should
  be shared by component and Playwright tests where practical.
- **axe-core integration** for automated component/page accessibility checks.
- **Playwright** for the few critical end-to-end and responsive paths.
- **ESLint with React hooks and JSX accessibility rules** for fast static
  feedback.

Recommended scripts:

```json
{
  "test": "vitest run",
  "test:watch": "vitest",
  "test:coverage": "vitest run --coverage",
  "lint": "eslint .",
  "e2e": "playwright test",
  "check": "npm run lint && npm run test && npm run build"
}
```

Avoid Storybook, React Query, Redux, Tailwind, a component framework, Motion,
GSAP, WebSockets, and a visual-regression SaaS for V1. The current stack plus
small hooks and vanilla CSS is sufficient.

## Smallest TDD task sequence

Each task is a thin red-green-refactor slice. Do not begin a later visual slice
while an earlier truth/contract slice is red.

### 1. Establish the harness without changing behavior

Write a smoke test that renders `App` in memory routing, a test setup with
jest-dom/MSW, and a build-safe Vitest configuration. Add one axe smoke test for
login. Make `test`, `lint`, and `build` callable in CI/Docker.

**Exit:** current app renders under tests; the known accessibility failure is
captured rather than waived.

### 2. Lock API paths, idempotency, and normalized errors

Write `client` contract tests for `/api/v1`, the required create/get/list/bundle
paths, `Idempotency-Key`, abort signals, bundle timeout, and stable `ApiError`
mapping. Delete tests and methods for partial regeneration.

**Exit:** no page needs to parse arbitrary Axios error shapes.

### 3. Lock a pure generation view model

Create table-driven tests for every lifecycle/status-stage combination, unknown
contract values, stable error codes, `TARGET_AMBIGUOUS`, and transport failure.
Implement a pure adapter/constants module.

**Exit:** there is exactly one canonical enum source and no elapsed-time input.

### 4. Replace fake polling and fake progress

With fake timers and MSW, test pending → processing real stages → done, a
transient network outage followed by recovery, terminal errors, no overlapping
requests, abort on unmount, and no fake ETA/percentage. Implement
`useGenerationPolling` and `GenerationTimeline`; remove local countdown and step
cycle.

**Exit:** the generation page only displays server truth and remains recoverable
through a transient browser/network failure.

### 5. Add ambiguity recovery

Test normalized overlay geometry at two responsive sizes, keyboard candidate
selection, the selected `target_hint`, a new idempotency key, and navigation to
the newly created generation. Implement the candidate selector without a
freehand editor.

**Exit:** every `TARGET_AMBIGUOUS` response has an explicit recoverable UI path.

### 6. Make campaign creation claim-safe

Test accessible file selection/drop, decode/type/size errors, canonical
categories, exactly one FR/EN language, optional verified-fact fields, semantic
steps, the review snapshot, product-success/generation-failure retry, and
double-submit idempotency. Extend the existing three-step page.

**Exit:** a retry cannot duplicate a product and the AI receives only the
declared product/campaign facts.

### 7. Replace the dashboard with real cursor history

Test one list request, every generation status, all generations for the same
product, cursor “load more,” correct resume/result routing, unknown-status
failure, and keyboard links. Remove N+1 enrichment.

**Exit:** history is authoritative and no generation is hidden behind a product.

### 8. Make result success strict

Test that non-`done`, incomplete manifest, missing copy, or missing bundle
readiness cannot render success. Test exact eight-file manifest recognition,
authenticated artifact blob lifecycle, exact aspect ratios, `object-fit:
contain`, three semantic platform tabs, platform-specific copy, copy feedback,
evidence disclosure, and bundle download errors.

**Exit:** the page can never present a degraded/incomplete result as successful.

### 9. Make regeneration immutable

Test that “Créer une nouvelle variation” creates one complete new generation
with `source_generation_id` and a fresh idempotency key, leaves the old result
unchanged, and navigates to the new generation. Remove text/decor mutation UI and
legacy API calls.

**Exit:** regeneration follows the locked complete-generation model.

### 10. Correct auth, shell, and dialog accessibility

Test requested-route restoration, invalid/undefined token handling, registration
partial success, keyboard password toggles, skip link/main landmark, French
not-found, mobile navigation disclosure, focus restoration, Escape, live
regions, and axe scans of every page state.

**Exit:** the complete workflow is keyboard-operable and has no serious/critical
automated accessibility violation.

### 11. Apply Cool Pearl Studio behind behavior tests

Write token-level contrast tests and Playwright screenshot assertions for a
small set of stable shells at 390, 768, and 1440 pixels. Introduce Instrument
Serif, Manrope, CA monogram, pearl/graphite/raspberry tokens, solid editorial
surfaces, restrained motion, and responsive rules. Remove obsolete glass,
orb/glow, gradient-text, emoji-control, hover-only, and dead CSS.

**Exit:** all previous behavior/accessibility tests remain green; target
breakpoints and reduced motion pass.

### 12. Prove the locked journeys end to end

Run Playwright against the versioned API contract for:

1. login and requested-route restoration;
2. French campaign creation through real stage changes to strict result/bundle;
3. English campaign rendering with `lang="en"`;
4. ambiguity selection and new-generation continuation;
5. AI service unavailable/runtime lost;
6. refresh/reconnect during a running durable job;
7. immutable variation visible alongside its source in cursor history;
8. keyboard-only execution at mobile and desktop widths.

**Exit:** frontend build, unit/integration tests, axe scans, and E2E checks pass
without fake fixtures being used in production code.

## Explicit non-goals for the frontend V1

To protect execution quality and YAGNI:

- no freehand mask editor;
- no fake ETA, percentage, or queue position;
- no WebSocket/SSE layer;
- no dashboard analytics, search, or filter system;
- no copy document editor or collaborative persistence;
- no per-platform visual regeneration;
- no dark mode;
- no client-side ZIP reconstruction;
- no public artifact URLs or query-string bearer tokens;
- no frontend UI framework or animation dependency.

## Contract gates before production implementation

These are not blockers to this audit, but they must be resolved in the backend
contract before the corresponding frontend TDD slice:

1. **Artifact preview access:** choose the authenticated per-file endpoint (the
   smallest fit for the current bearer client) or a same-site cookie strategy.
2. **Ambiguity continuation:** confirm that selecting a candidate creates a new
   complete generation with `source_generation_id` and normalized `target_hint`,
   and define where candidates/input snapshot appear in `GET /generations/{id}`.
3. **Result JSON:** lock the platform-specific parsed copy schema and manifest
   file-record shape returned by the generation endpoint.
4. **Canonical enums:** lock category and art-direction codes so
   `hygiene`/`hygiene_deo` and `classique`/other spellings cannot drift.
5. **API origin:** choose direct
   `http://localhost:8000/api/v1` or same-origin nginx `/api/v1` proxying. If
   same-origin is selected, update nginx before changing the client base URL.

## Audit disposition

**Baseline readiness:** suitable for incremental TDD refactoring, not suitable
for acceptance as the locked V1.

**Immediate implementation priority:** API contract tests → truthful generation
state/polling → ambiguity → claim-safe upload → real history → strict results →
accessibility shell → Cool Pearl Studio visual pass.
