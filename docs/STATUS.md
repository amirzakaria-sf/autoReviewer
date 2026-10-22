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

- **As of:** 2026-10-22
- **Last agent:** claude
- **Commit:** `3b58fdce679c39dc186b4994bf2b8864e80e2c3a` (`3b58fdc`)
- **Deployed:** **yes**, on a **new host**. `https://whip-guard.zakarias.in` now points at
  the shared AWS VM `13.60.61.135` (eu-north-1). The old Azure VM (`172.206.16.54`) was
  unreachable and its database was **not** migrated: this host started empty, with one
  bootstrapped admin. Deployed with `./deploy.sh --no-pull` from the working tree that
  became this commit.

- **Shipped this session (claude):**
  - **Bedrock as the model provider** (`LLM_PROVIDER=bedrock`), inside `app/azure_client.py`:
    mantle Chat Completions, SigV4 with the instance role, forced-function structured
    output, per-model 400 degrade, merged assistant/tool-call replay. Kimi K2.5 = fast,
    GLM-5 = worker + planner. Azure path untouched and still the test default.
  - **Off on Bedrock, openly:** web research (`research.web_research_available()`) and dense
    retrieval (`embeddings.available()`). See DECISIONS 2026-10-22.
  - **Three latent bugs the move exposed:** `main.py` never created the `vector` extension
    (fresh DB could not boot); `_JUROR_RUBRIC` was dead code, so no model was ever told the
    0-100 scale (now wired, plus `Field` descriptions in the schema); `deploy.sh` waited on
    `/healthz`, which the OLD backend answers, so it printed "finished OK" before the swap.
  - Two poller tests depended on the host `.env` having `GITHUB_TOKEN`. Pinned in-test.
  - **Landing page rebuilt** (`app/page.tsx`, `components/landing/*`): simulator, jury lab,
    guardrail demos. Thresholds, the 30-point rule, write scopes and secret patterns are
    copied from the backend into `sim-data.ts`. **If you change one of those, change it
    there too.**
  - **Host setup:** `.env` (generated; never committed), `/srv/workspace`, nginx vhost from
    `nginx/whip-guard.conf`, Let's Encrypt cert, Cloudflare A record. The showcase vhost for
    this hostname moved to `/etc/nginx/disabled/`, and its line in
    `~/demoStuff/demoApps/deploy/vhosts.tsv` was commented out (**uncommitted in that repo**).

- **Verified this session:**
  - `pytest -q` → **412 passed** (401 before plus 11 new). `tsc --noEmit` clean, `next build` clean.
  - Live Bedrock through the app's own code, from the venv and **from inside the backend
    container** (instance role reaches containers): Arbiter `score=95`, weights summing to
    95; Skeptic `confidence=85`; a 2-tick `read_file` tool loop on GLM-5 that answered.
    `council_runs` recorded `kimi-k2.5 213/253 tokens protocol=bedrock`.
  - Detection eval on this host (work item `ba83a5b5`): **recall 1.0, precision 1.0**, all
    six categories detected-when-broken and clean-when-fixed, 137 s. (The first run's UI
    timeout was the cold 2.3 GB Playwright image pull.)
  - Public: `/`, `/login`, `/api/auth/session`, manifest all 200 over valid TLS, http → 301.
    Login wrong password 401, right 200. `/api/repos`, `/api/push/status` 200. Websocket
    through nginx: no cookie refused (403), cookie → 101 and held open. Sibling sites still
    200 after the nginx reload.
  - Brevo SMTP authenticates (login only, nothing sent).
  - Landing in headless Chromium at 1440 and 390: no console errors, no document overflow,
    page scrolls, simulator driven end to end (push → decision → approve → "Pull request
    open").
  - `./deploy.sh --no-pull` twice; the second, with the fix, finished only after the
    backend container ID changed.

### Needs verification

- **No GitHub identity on this host.** `GITHUB_TOKEN`, the GitHub App and the OAuth App are
  all unset, so no repository can be connected, the poller idles, and nothing has run the
  Fix Council's patch loop on Bedrock against a real repo. The jury, Arbiter and a tool loop
  ARE verified on Bedrock. To prove the rest, connect a repo, push a seeded bug, and watch
  for `AWAITING_APPROVAL` with `protocol: bedrock` rows in `council_runs`.
- **Cloudflare Pages:** the token in `.env` is DNS-scoped and returns `Authentication error`
  on the Pages API, so post-approval preview deploys will fail until a token with
  `Account / Cloudflare Pages / Edit` replaces it.
- **GitHub webhook:** `GITHUB_WEBHOOK_SECRET` is now set on this side. Any GitHub hook
  pointing here must be given the same secret, or its deliveries will be rejected (correctly).
- **Slack:** unconfigured on this host (reads as absent, by design).
- Push on a real device (unchanged from last session).
- Memory: ≈1.2 GB free on a host running ~25 other containers. A Fix Council plus a
  Playwright sandbox has not been run under that pressure. Disk is 80% used (21 GB free).

### Other agent should

- **Hot files:** none. Nothing is in flight.
- Read the new §11 entry "One model client, two providers" in `docs/info.md` before
  touching `azure_client.py`. Production is Bedrock; the tests run Azure.
- Do not reintroduce `web_search` or embeddings calls on the Bedrock path without a real
  source. DECISIONS 2026-10-22 explains why they are off.

### Blocked on the user (not either agent)

- A GitHub credential: a PAT, or better a GitHub App (ID, private key, installation), and an
  OAuth App whose callback is `https://whip-guard.zakarias.in/api/github/oauth/callback`.
- A Cloudflare token with Pages edit rights.
- Slack app tokens, if wanted. The Interactivity URL is
  `https://whip-guard.zakarias.in/api/slack/interactions`.
- Admin login: `~/ai/whipguard-admin-credentials.txt` (mode 600, outside the repo).

---

## Recent (newest first)

- **2026-09-21 (claude, `17460b2`)** — Azure Responses, `apply_patch`, the research
  council, the tenancy boundary closed, PWA + push + mobile. Fix Council ran end to end on
  issue #21 at resolution 100/100 on the old host.

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
