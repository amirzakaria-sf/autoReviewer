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

- **As of:** 2026-09-20
- **Last agent:** claude
- **Commit:** `732f41c4b4956156c98b8a0dc5be5a4fc451803e` (`732f41c`) — authored by cursor;
  this session reviewed it rather than adding code.
- **Deployed:** `732f41c` is live. It was committed 2026-09-19 06:32 and sat **undeployed
  for ~22 hours** (containers were still running the 2026-09-18 build). Claude built and
  deployed both images on 2026-09-20; boot clean, `schema_sync` applied
  `webhook_deliveries`, `repos.cloudflare_pages_project` and
  `organizations.github_app_installation_id` automatically. All seven API surfaces 200.

- **Docs commit:** `0545a6ae5a8260c40bf9e006b8c7c3c9d3b81030` (`0545a6a`) — this protocol.
- **Shipped this session (claude):** the two-agent documentation flow itself — `docs/info.md`,
  this file, `DECISIONS.md`, `ARCHITECTURE.md`, `CHANGELOG.md`, both agent folders,
  `CLAUDE.md` and `AGENTS.md`. No application code changed.

- **Reviewed this session (claude):** cursor's `732f41c` (52 files, +2049/−447). Verdict:
  relevant and correct. It closed four real defects, each of which had passed tests and
  review before:
  1. `/api/webhooks/github` accepted unsigned payloads from anyone — the path is public by
     necessity and had no HMAC check. A forged `pull_request closed merged` could mark
     fixes merged, delete branches and delete Cloudflare deployments. Now verified.
  2. The cross-app outcome checker hardcoded `cloudflare_state = {"reachable": True}` — it
     asserted a fact it never checked. `app/preview.py` now probes the URL.
  3. The secret scanner put the **matched secret value** into evidence, which is copied to
     a GitHub issue body, the dashboard and Slack. Now redacted to `path:line: label`.
  4. Three more `settings.fixture_repo` fallbacks in `approval_graph` and `runner`.
  Also: durable webhook dedupe, per-repo Cloudflare project, per-org GitHub App
  installation with per-installation token caching, a global kill switch, detector command
  discovery for non-Node repos, and `ask_human` now genuinely blocking the Fix Council.
  It changed `deps.visible_repo_ids` to `repo_ids_for_user` — checked, the tenancy boundary
  holds and it is more robust than what it replaced.

- **Verified this session:** `pytest` 270 passed. `tsc --noEmit` clean. Deploy clean, boot
  log free of errors. Global kill switch exercised live (paused detection → scan returned
  409 → unpaused). Webhook dedupe exercised live (replay returned `deduped`, row written to
  `webhook_deliveries`, test row removed afterwards). `verify_webhook_signature` exercised
  directly: valid accepted, wrong rejected, tampered body rejected, empty header rejected.
  `boot_checks.refuse_insecure_defaults()` checked against the live `.env` before deploying
  — it passes, so it would not have blocked the boot.

### Needs verification

- **Nothing from cursor's commit is unexercised** except the two items below, which are
  blocked on configuration rather than on effort.
- `GITHUB_WEBHOOK_SECRET` is **unset on both sides**, so the signature check added in
  `732f41c` is inert — the endpoint still accepts unsigned POSTs (confirmed: HTTP 200).
  Arming it needs a secret in `.env` **and** in the GitHub webhook config. Until then the
  fix is correct but not protecting anything.
- Alembic is scaffolded but not wired into `deploy.sh`; `schema_sync` remains the live
  path. `alembic upgrade head` has never been run here.

### Open — tenancy gaps (claude, found 2026-09-20, not yet fixed)

`repos.org_id` scoping was added to `routers/api.py` and `routers/fix_review.py`. Three
routers were missed and are still unscoped. These are real cross-org reads:

- **`routers/counsel.py`** — the worst. `_resolve_context` takes `repo_id` with no
  ownership check, and with none passed falls back to `select(Repo).first()` — an arbitrary
  repo from the whole deployment. Counsel has `read_file`, `git log` and code search, so
  one org can read another's source through it.
- **`routers/ws.py`** — `broadcaster.broadcast` sends every activity event to every
  connected client, across orgs.
- **`routers/human_input.py`** — `list_requests` returns all clarification requests
  unscoped.

`routers/email_actions.py` and `routers/webhooks.py` are fine: their signed token or
signature *is* the credential.

### Other agent should

- Pick up the three tenancy gaps above if the user asks for them — `counsel.py` first.
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
