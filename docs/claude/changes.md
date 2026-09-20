# Claude changes

Newest first. Template: see [`../info.md`](../info.md) §5.

Both agents run on this server, so an entry here should carry real evidence — commands run
against the live deployment, log lines, query output. Cursor cannot see this terminal; this
file is the only channel.

---

## 2026-09-20 06:55 (UTC) — Adopt the two-agent documentation flow

- **Agent:** claude
- **Commit:** `0545a6ae5a8260c40bf9e006b8c7c3c9d3b81030` (`0545a6a`)
- **User ask:** implement in WhipGuard the multi-agent documentation flow already running
  in `aiClass`, `opencode` and `jobFlowAuto`, so both agents stay in sync.
- **Shipped:**
  - `docs/info.md` — the protocol. §0–§10 are the canonical version shared with the three
    sibling repos, deliberately unchanged so a lesson learned in one is legible in all four.
    §11 is WhipGuard-specific and is the part worth reading: the security split, the
    worker's shared image tag, tenancy, the forbidden actions, mirror-clone git, the
    verification gates, secrets in evidence, and the test database.
  - `docs/STATUS.md` — seeded with the real current state, including the deploy gap found
    this session and the three open tenancy gaps.
  - `docs/DECISIONS.md` — 24 rows recovered from this repo's actual history, each with the
    failure that produced it.
  - `docs/ARCHITECTURE.md` — short two-process / router / council / workspace map, with the
    org-scoping status of every router.
  - `docs/CHANGELOG.md` — six dated slices back to 2026-09-15.
  - `docs/{claude,cursor}/` — README, changes, notes, architecture scratch, plans/, specs/.
  - `docs/{plans,specs,archive}/README.md`.
  - `CLAUDE.md`, `AGENTS.md` — pointers, per §9. Deliberately thin.
  - `.gitignore` — `frontend/tsconfig.tsbuildinfo` (a build artifact that shows up dirty on
    every build and had already been committed once).
- **Docs updated:** all of the above are new. No application code changed.
- **Verified:**
  - `git log --format='%h %ad'` — confirmed only `732f41c` is Cursor's; `3a670ab`
    (12,267 insertions) is Claude's own prior work committed by the user.
  - `backend/.venv/bin/python -m pytest -q` → **270 passed**.
  - `npx tsc --noEmit` in `frontend/` → clean.
  - `docker compose build backend frontend && docker compose up -d` → all four containers
    up, boot log free of errors, `Application startup complete`.
  - Seven API surfaces after deploy: `/api/overview`, `/api/issues`, `/api/repos`,
    `/api/org`, `/api/org/identity-links`, `/api/admin/overview`, `/api/admin/orgs` — all
    200.
  - `schema_sync` applied Cursor's new schema on boot: `webhook_deliveries` exists
    (`SELECT count(*)` → 0), `repos.cloudflare_pages_project` exists.
- **NOT verified:** nothing outstanding from this session — it is documentation.
- **Not done / watch-outs:** the three tenancy gaps in STATUS are *not* fixed, only
  recorded. `counsel.py` is the urgent one.
- **Hot files:** none.

---

## 2026-09-20 05:00 (UTC) — Review of Cursor's `732f41c`

- **Agent:** claude
- **Type:** review
- **Commit:** `0545a6ae5a8260c40bf9e006b8c7c3c9d3b81030` (`0545a6a`) — recorded with the protocol commit
- **Reviewing:** `732f41c4b4956156c98b8a0dc5be5a4fc451803e` — 52 files, +2049/−447.
- **User ask:** go through Cursor's changes in depth and say whether they were relevant.
- **Verdict: relevant and correct.** It closed four defects that had each passed tests and
  review:
  1. **`/api/webhooks/github` accepted unsigned payloads.** The path is public by necessity
     (`main.py` `_PUBLIC_PATHS`) and had no HMAC check, so a forged
     `pull_request closed merged` could mark fixes merged, delete branches and delete
     Cloudflare deployments. Now verified against `X-Hub-Signature-256`.
  2. **The outcome checker asserted `cloudflare_state = {"reachable": True}`** — hardcoded.
     The cross-app check that fails a run closed on disagreement was stating a fact it never
     checked. `app/preview.py` now probes the URL.
  3. **The secret scanner copied the matched secret into evidence**, which is written to a
     GitHub issue body, the dashboard and Slack. Now `path:line: label`.
  4. **Three surviving `settings.fixture_repo` fallbacks** in `approval_graph` (×2) and
     `runner`.
  Also landed: durable webhook dedupe (`webhook_deliveries`), per-repo Cloudflare project,
  per-org GitHub App installation with a per-installation token cache, a global kill switch,
  detector command discovery so non-Node repos are not forced through `node --test`,
  blocking `ask_human` (`needs_human` → `AWAITING_CLARIFICATION` → answering re-queues the
  run), Alembic scaffolding, and a boot refusal on demo credentials. 27 new tests.
- **Checked carefully because it touched a security boundary:**
  `deps.visible_repo_ids` was changed from `repo_ids_for_org(memberships[0])` to
  `repo_ids_for_user(user_id)`. It joins through `org_members`, so the tenancy boundary
  holds, and it is more robust than what it replaced if multi-org ever lands. The isolation
  tests still pass.
- **Verified:**
  - 270 tests pass; frontend typechecks.
  - **It had never been deployed.** Containers were 33 hours old, predating the commit by
    ~22 hours. Built and deployed both images; clean boot, schema applied automatically.
  - Global kill switch, live: `POST /api/admin/kill-switch {"detection_paused":true}` →
    `{"detection_paused":true,...}`; `POST /api/repos/<id>/scan` → **409 "detection is paused
    globally"**; unpaused afterwards.
  - Webhook dedupe, live: first POST 200, replay of the same `X-GitHub-Delivery` →
    `{"ok":true,"deduped":true}`, row present in `webhook_deliveries`. Test row deleted.
  - `verify_webhook_signature` directly: valid accepted, wrong rejected, tampered body
    rejected, empty header rejected.
  - `boot_checks.refuse_insecure_defaults()` against the live `.env` **before** deploying —
    it passes, so it would not have blocked the boot. Worth checking first: on a box still
    carrying the demo password it is a hard startup failure.
- **NOT verified:** the signature check is **inert** — `GITHUB_WEBHOOK_SECRET` is unset in
  `.env` and no secret is configured on the GitHub webhook, so the endpoint still accepts
  unsigned POSTs (confirmed: HTTP 200). Arming it is a user action on both sides. Alembic
  has never been run here; `schema_sync` remains the live path.
- **Found, not fixed — three tenancy gaps** (mine, from the 2026-09-18 scoping pass, which
  Cursor did not touch): `counsel.py` resolves a repo with no ownership check and falls back
  to an arbitrary repo deployment-wide, and it has `read_file`, `git log` and code search;
  `ws.py` broadcasts every activity event to every client; `human_input.py` lists all
  clarification requests. Recorded in STATUS.
- **Hot files:** none.
