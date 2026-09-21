# STATUS — living handoff

Both agents overwrite the **Current** block when they finish a session that changed the
repo. Older history goes under **Recent** as short bullets; do not write a novel.

Protocol: [`info.md`](./info.md).

Two fields need care:

- **Deployed** — what is actually running on `whip-guard.zakarias.in`, which on this repo
  is **not** the same as what is on `main`. `deploy.sh` builds from the working tree, so a
  commit does not imply a deploy and a deploy does not imply a push. Whoever does either
  writes it here.
- **Needs verification** — anything shipped but not exercised. The next session clears it
  by running it, or replaces it with what was found.

---

## Current

- **As of:** 2026-09-21
- **Last agent:** claude
- **Deployed:** **yes** — `8c39e71`, deployed 09:24 UTC and verified against the live host.
  `create_all` created `research_findings` and `push_subscriptions` at boot; no manual
  migration either time. Latest deploy `17460b2`, 10:26 UTC.

- **Shipped this session (claude):**
  - **Azure Responses + `apply_patch` + a curated web-research council** (`6093ea8`,
    `fbcc5b6`). Generation moved to the Responses API so a tool-bound call can carry native
    reasoning, which Chat Completions cannot do on GPT-5.x — the patch loop had never
    thought. `apply_patch` replaced `write_file` as the default edit. Research is a Gatherer
    plus a Curator that attributes every claim to a source the search actually returned.
  - **The tenancy boundary closed** (`7dab810`, `355359e`). Counsel, the activity websocket,
    the clarification endpoints and the GitHub connect surface. Seven holes, two of them
    writes rather than reads.
  - **`apply_patch` permissions** (`634699a`). It was stripping the mode and owner of every
    file it wrote, so the sandbox could not read back a correct patch. Found by running a
    real Fix Council, not by reading the code.
  - **Workspace relocation** (`858f8f8`). A failed org lookup could move an entire
    repository tree into `_unassigned` and the next call would move it back.
  - **PWA + mobile + push** (`8c39e71`). See below.

- **The Fix Council ran end to end under the new protocol** — issue #21, the seeded
  backend total-calculation bug. `AWAITING_APPROVAL`, **resolution confidence 100/100**,
  first attempt, no retry, no failure traces. Three patch ticks plus an Arbiter, every turn
  `protocol: responses` with reasoning spent and the prompt cache hitting (3.9k cached
  tokens by tick two). The Arbiter's rubric names the actual change. **That fix is waiting
  for a human — it is the one thing on the dashboard asking for a decision.**

- **Mobile and PWA:** every screen laid out for a phone for the first time. The app had no
  viewport meta at all, so nothing had ever been *rendered* at that width. Bottom tab bar
  (the header nav was `hidden sm:flex` with nothing behind it — a phone had no navigation),
  tables that become cards, 16px inputs, 44px targets, safe-area insets. Installable:
  standalone manifest, maskable icons, offline shell, a service worker that never caches
  `/api/`.

- **Push notifications:** `pywebpush` behind `app/push.py`, subscriptions keyed on endpoint
  and re-bound to the current account on every authenticated session. Wired into the three
  existing notification sites inside the same `should_notify` guard. Per-device toggle in
  Profile → Notifications that requests permission itself, plus a test button. Uses the same
  VAPID keypair as the sibling `opencode` deployment — a VAPID key identifies the sender,
  not the app.

- **Verified this session:**
  - `pytest -q` → **401 passed**. `tsc --noEmit` clean. Production build clean.
  - **Second audit after calling it done** (the first two times that question was asked,
    the answer was wrong). Checked by a different route than my own notes: which routes had
    actually been rendered, and which code had actually been tested. Ten of sixteen routes
    had never been viewed at phone width; the push router had no tests at all. Both closed —
    all sixteen rendered at 360px, eleven tests on subscription ownership.
  - Live: six screens at 390×844, no document overflow, no console errors. Manifest, service
    worker, all four icons and `/api/push/status` all served; the worker registers and is
    active at scope `/`.
  - The VAPID keypair signs a real JWT, and **the public key derives from the private one** —
    a genuine pair, not two values that happen to sit next to each other in `.env`.
  - Counsel answered live with a correct `app.js:22` citation; both turns recorded
    `protocol: responses`, with 1,207 cached input tokens on the second.
  - The websocket contract end to end through nginx and the NOTIFY relay: no cookie refused,
    valid cookie connects, own-repo event arrives, other-org event does not, unaddressed
    event does not.

### Needs verification

- **The browser's own `pushManager.subscribe()`.** Headless Chromium has no push service
  connection and fails with "permission denied" regardless of permission, so this one hop
  cannot be exercised here. Everything either side of it is verified. It needs a real
  device: install to a home screen, Profile → Notifications → enable → **Send a test
  notification**. If that arrives, the whole path works.
- `GITHUB_WEBHOOK_SECRET` is **unset on both sides** — absent from `.env`, and GitHub's hook
  (id `678747728`) reports `"secret_set": false`. The endpoint accepts unsigned POSTs from
  anyone who knows the URL, and a forged `pull_request closed merged` marks fixes merged,
  deletes branches and deletes Cloudflare deployments. **Still the highest-priority item on
  the user's side.**
- Alembic is scaffolded but not wired into `deploy.sh`; `create_all` + `schema_sync` remain
  the live path and have now handled two new tables without a manual step.

### Closed — the tenancy gaps (claude, 2026-09-20, `7dab810`)

`counsel.py`, `ws.py` and `human_input.py` are scoped. Details in **Shipped** above.
`email_actions.py` and `webhooks.py` remain correct as they were: their signed token or
signature *is* the credential.

Two things a reader should carry forward rather than rediscover:

- **`@app.middleware("http")` does not run for websockets.** Any new websocket route
  authenticates itself or is public. There is no middleware that will catch it.
- **An activity event with no `repo_id` reaches nobody.** A new `emit_event` added outside
  an `activity_scope` will go missing from the feed rather than leak. The broadcaster logs
  each one — read that log before concluding the feed is broken.

### Other agent should

- **Nothing is queued.** The tenancy gaps are closed; ask the user what is next rather than
  picking something up.
- **Never construct an `AzureOpenAI` outside `app/azure_client.py` and `app/embeddings.py`.**
  That rule was silently false for months in `fix_council.py` and `counsel/agent.py`, and the
  cost was invisible: two of the most expensive call sites in the product recorded no usage
  at all. If a new call site needs the model, it needs `complete_turn`.
- Callers speak **Responses input items**. A tool result is `function_call_output`, a
  function call's `arguments` is a JSON *string*, and reasoning items are replayed with `id`
  and `summary` only. Each of those is a 400 in production and invisible against a mock.
- Read `docs/info.md` §11 before touching the worker, the workspace layout, or anything
  that writes to GitHub.
- **Hot files:** none. Nothing is in flight.

### Blocked on the user (not either agent)

- Slack: Interactivity Request URL → `https://whip-guard.zakarias.in/api/slack/interactions`,
  plus app distribution before another workspace can install it.
- GitHub App private key — until it is set, the bot pushes and comments as the human
  account that owns the PAT.
- PRs #5 and #11 on the fixture repo are litter from the pre-review flow. Nothing new
  accumulates: under the current flow no PR exists until approval.

---

## Recent (newest first)

- **2026-09-20 (claude, `0545a6a`)** — adopted the two-agent documentation protocol:
  `docs/info.md`, this file, `DECISIONS.md`, `ARCHITECTURE.md`, `CHANGELOG.md`, both agent
  folders, `CLAUDE.md` and `AGENTS.md`. Also deployed `732f41c`, which had sat undeployed
  for ~22 hours, and reviewed it (verdict: relevant and correct — four real defects closed,
  including a webhook endpoint that accepted unsigned payloads and a secret scanner that put
  the matched secret into evidence).

- **2026-09-19 (cursor, `732f41c`)** — webhook signature verification + durable delivery
  dedupe, real Cloudflare preview probe, secret redaction, remaining fixture-repo fallbacks,
  global kill switch, per-repo Pages project, per-org GitHub App installation, blocking
  `ask_human`, Alembic scaffolding, boot check on demo credentials. 27 new tests.
- **2026-09-18 (claude)** — org onboarding end to end: platform admin creates orgs, org
  admins invite by email, `/join` accepts an invitation into a live session, repos inherit
  the connector's org. Then the three org-configuration surfaces that had been read-only:
  designations, routing rules, and git identity links with suggestions read from real
  repository history.
- **2026-09-17 (claude)** — the three-verb review flow (approve / reject / **ask for
  changes**) modelled on the sibling `opencode` project's plan approval. Nothing reaches
  GitHub until approval. Found and fixed four bugs in the approve path that had never once
  completed end to end.
- **2026-09-17 (claude)** — workspace moved to the data disk under `<org>/<repo>/`, with
  worktree link repair on relocation.
