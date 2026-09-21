# WhipGuard

**An AI bug council that watches a connected repository, detects real problems
across a category registry, proposes a verified fix, and only ships after a
human approves and a live re-check confirms the fix actually holds.**

Live: **https://whip-guard.zakarias.in**

The original design essay is [`plan.md`](./plan.md). That document is the
*why*; this README is the *what ships today*. Where they disagree, the code
wins, then this file.

**Working on this repo?** Two agents do — Cursor and Claude — and they share a
filesystem but not a session. Read [`docs/info.md`](./docs/info.md) before you
start: it is the protocol, and §11 lists the traps that have each already cost a
debugging session. The live handoff is [`docs/STATUS.md`](./docs/STATUS.md).

---

## Table of contents

1. [What this is](#what-this-is)
2. [What is live vs still fixture-shaped](#what-is-live-vs-still-fixture-shaped)
3. [Agentic harness](#agentic-harness)
4. [Process split](#process-split)
5. [External apps](#external-apps)
6. [Dashboard](#dashboard)
7. [Repository layout](#repository-layout)
8. [Setup](#setup)
9. [How reliability is tested](#how-reliability-is-tested)
10. [Security & scope](#security--scope)
11. [Known gaps](#known-gaps)

---

## What this is

A connected repo is scanned per enabled category. A finding that clears that
category's assurance threshold becomes a real GitHub issue. A Fix Council
turns that issue into a patch, scored by a second jury that **re-runs the
same detector** against the patched worktree. A human approves from the
dashboard, Slack, or a signed email magic link. On approval WhipGuard pushes
the **exact approved patch** (never regenerated), opens a draft PR, deploys
the branch to Cloudflare Pages, and re-runs the same check against the live
preview URL. A final **cross-app outcome checker** reads GitHub, Cloudflare,
Slack, and the dashboard row back independently and fails the run closed if
they disagree — even if every upstream step reported success.

WhipGuard **never merges**. There is no `merge_pr` on the GitHub client.
A human merge on GitHub is reflected via webhook as `merged`, not claimed as
WhipGuard's own action.

Login is app-level: email/password, httpOnly cookies, rotating refresh
tokens. Organizations, designations, seniority routing, and invites exist.
Counsel is a read-only analyst over the same indexes; it is not a judge and
cannot approve a fix.

---

## What is live vs still fixture-shaped

Six categories are registered in `backend/app/categories.py`. They are not
equally deep:

| key | Detector (what actually runs) | Write scope |
|-----|-------------------------------|-------------|
| `ui` | Playwright in a Docker sandbox (`playwright test`, excluding `@a11y`) | frontend (fixture globs) |
| `accessibility` | axe-core Playwright suite (`tests/accessibility.spec.ts`) | frontend |
| `backend` | Discovers `pytest` / `go test` / `cargo test` / `node --test` from layout markers; the fixture still hits `node --test backend/*.test.js` | backend JS (fixture layout) plus those other runners |
| `security` | in-process regex secret scan (values redacted from evidence); `gitleaks` if the binary is on PATH; GitHub Dependabot alerts merged in | any file, narrow diff |
| `performance` | shipped `.js` byte-budget check (not Lighthouse) | whichever file grew |
| `documentation` | README backtick-calls vs the symbol table | `*.md` only |

UI and accessibility execute the repo's own browser tests. Backend picks a
runner from the worktree instead of always using the fixture glob. Security
is still a static scan first; gitleaks and Dependabot are additive, not a
replacement. Performance / documentation stay honest static heuristics.

Default `entry_files` and `FIXTURE_REPO` still name `whipguard-demo-ui`
(`app.js`, `backend/calculate.js`, …) so that checkout is unchanged. When
those files are missing, retrieval infers entry points from the worktree.
Cloudflare Pages can be set per repo; empty falls back to
`CLOUDFLARE_PAGES_PROJECT`.

---

## Agentic harness

### Graphs

`bug_council.py`, `fix_council.py`, and `approval_graph.py` are LangGraph
`StateGraph`s with conditional edges. Side effects (raise issue, notify,
propose) live in `run_and_persist` / `runner.py` / `resolve_approval()`, not
as decorative graph nodes.

```
BugCouncilGraph:
  START -> Detect -> [Skeptic ∥ Corroborator] -> MechanicalRecheck -> Arbiter
        -> disagreement? MetaAudit (Ask Mode / clarify)
        -> score >= category assurance? raise : hold

FixCouncilGraph:
  START -> Retrieval (hybrid + graph neighbourhood)
        -> PatchGeneration (ReAct: read_file / apply_patch / write_file / lookup_docs /
                            research_web / ask_human / finish_patch)
        -> Verifier (the category detector, not a model)
        -> Arbiter
        -> score >= resolution threshold? propose
           else retry_prepare -> patch again (one bounded retry), then hold
        -> ask_human pauses the graph; resume is a fresh attempt with the answer as feedback

ApprovalGraph (worker, apply=True):
  FreshnessCheck -> ApplyPatch -> OpenDraftPR -> CloudflareDeploy
        -> PostDeployOracle (same detector, PLAYWRIGHT_BASE_URL=preview)
        -> OutcomeCheck -> verified | verification-failed | outcome-check-failed
```

Detect and mechanical recheck are **not** model calls. They run in a
throwaway Playwright image via `app/sandbox/docker_runner.py`. A flake that
passes on rerun is dropped in code before a second model call
(`tests/test_bug_council_graph.py`).

### Model protocol

Every generation call goes through `app/azure_client.py::complete_turn`, over the
**Responses API** (`{endpoint}/openai/v1/`, `api_version="preview"`). That is what lets a
tool-bound call carry native reasoning: on GPT-5.x, Chat Completions cannot combine the
two, so the patch loop used to run with reasoning off. Reasoning items are replayed across
ticks, tool schemas go out flat with `strict: false`, jury and Arbiter output is a forced
Pydantic function rather than JSON asked for in prose, and `prompt_cache_key` carries the
partition key `prompts.partition_key()` has always computed.

A `400` drops the one optional parameter its body names and retries once; anything else
raises with the body logged. `WHIPGUARD_AZURE_API=chat` pins the whole process to Chat
Completions as an operator hatch. Embeddings stay on their own client.

`apply_patch` is the preferred edit: an exact, unique anchor replaced in place. `write_file`
remains for creating a file or a genuine full replace. Both refuse a path outside the
worktree and a path outside the category's write scope. Nothing here runs a shell — the
verifier runs the category's detector after `finish_patch`, and that result decides.

### Web research

`app/research.py` gives the PRD council, Counsel and the patch worker one tool for things
the repository cannot answer — whether an API still exists, what a third-party service
requires, what a release changed. It is two roles, not one call:

- **Gatherer** runs the model's own `web_search` tool and reports claims with sources.
- **Curator** attributes every claim to a URL the search actually returned, scores
  relevance, recency and source authority, and drops the rest.

A claim whose URL was never returned is dropped mechanically, whatever the Curator decided.
Keeps and rejections are both written to `research_findings`, so a PRD's external claim is
traceable to a source the same way a code claim is traceable to `path:line`. Nothing
decides *when* to research from a keyword list — the PRD planner judges it per requirement
and the tool is autonomous elsewhere.

### Prompt contract

`backend/app/prompts.py`:

- `build_prefix(role, workspace_map, category_rules)` — byte-identical for
  identical inputs.
- `pad_to_cache_floor(prefix)` — pad with already-static content until the
  provider cache floor (~1024 tokens).
- The workspace map is computed in `workspace_map.py`, not discovered by
  tool calls.
- Oversized rules **fail a test** (`test_oversized_rules_fail_not_truncate`)
  rather than silently truncating.

`prompt_compiler.py` budgets evidence and briefings. `llm_cache.py` caches
deterministic jury calls.

### Model routing

| Role | Deployment |
|------|------------|
| Skeptic / Corroborator | `AZURE_FAST_DEPLOYMENT` |
| Patch generation | `AZURE_WORKER_DEPLOYMENT` |
| Arbiter / Meta-auditor | `AZURE_PLANNER_DEPLOYMENT` |
| Embeddings | `AZURE_EMBEDDING_DEPLOYMENT` |

The Arbiter returns a Pydantic `ArbiterVerdict`: score, `{factor, weight,
note}` line items, verdict, optional `needs_clarification`. The dashboard
rubric is those rows in `CouncilRun`, not parsed prose.

### Retrieval (live, not the old static-only cut)

Fix Council retrieval is **hybrid**:

- dense chunks in **pgvector** (`retrieval.py` / `embeddings.py`)
- BM25 lexical index (`lexical_index.py`)
- one-hop import / dependent graph in Postgres (`graph_index.py`)
- fused in `hybrid_retrieval.py`

`context_broker.py` builds a briefing from that plus `memory_traces`
(negative outcomes: below-threshold, verify-fail, reject, outcome-fail).
Context7 (`lookup_docs`) is an optional library-docs tool on the ReAct loop.

### Human in the loop

`resolve_approval(fix_id, approved, actor, surface)` is the **one** function
the dashboard, Slack buttons, and email magic links call. First surface
wins; a second call returns `already_handled`.

Fix review (`fix_review.py`) is a thread: approve, reject, or ask for a
different approach. Revisions run on the worker.

Arbiter `needs_clarification` holds the issue as `awaiting-clarification`
until a human answers. The Fix Council `ask_human` tool currently **records**
a request but does **not** pause the ReAct loop.

### Outcome checker

`graphs/outcome_checker.py` is four reads and a comparison, no model:

- GitHub: issue exists, PR open, `Closes #N`, **not merged**
- Cloudflare: the post-deploy assertion result (`reachable` is still
  hardcoded `True` — the Playwright re-check is the real gate)
- Slack: thread text mentions current status, **if Slack is configured**
- Dashboard: `Fix.status`

Any mismatch → `outcome-check-failed`.

### Externally filed bugs

`poller.py` (on the worker) lists open GitHub issues labeled
`whipguard:fix-me` per connected repo and enqueues the same Fix Council path.
Today those rows are stored as `category="ui"` regardless of other labels.

---

## Process split

Four Compose services:

| Service | Role | `docker.sock` |
|---------|------|----------------|
| `postgres` | `pgvector/pgvector:pg16` | no |
| `backend` | FastAPI `:8300`, workspace **read-only**, retrieval cache rw | **no** |
| `worker` | `python -m app.worker` — scans, sandboxes, deploys, poller, sweeper, calibration | **yes** |
| `frontend` | Next.js `:3300` | no |

The worker has **no inbound port**. The web process may only insert a
`work_items` row. Worker and backend share one image tag
(`whipguard-backend:latest`) so `docker compose build backend` cannot leave
the worker on a stale image.

Host ports: frontend `3300`, API `8300`, Postgres `127.0.0.1:5433`.

---

## External apps

These are the surfaces WhipGuard **writes**. Azure OpenAI is the model, not
an app.

| App | What it does |
|-----|----------------|
| **GitHub** | Labeled issue, draft PR (`Closes #N`), comments, branch push/delete. GitHub **App** installation token is preferred when `GITHUB_APP_*` is set (per-org install id on the org, else process-wide); otherwise PAT / OAuth token on `GITHUB_TOKEN`. Webhooks: `push`, `pull_request`, `issues`, `issue_comment`. HMAC via `X-Hub-Signature-256` when `GITHUB_WEBHOOK_SECRET` is set. Delivery ids live in `webhook_deliveries` plus an in-memory set. Issue lookups are `(repo, number)`. |
| **Cloudflare Pages** | `wrangler pages deploy --branch` → per-branch preview URL. Per-repo project name, falling back to `CLOUDFLARE_PAGES_PROJECT`. Reachability is a GET `< 500`; Playwright (or the category detector) is still the real gate. |
| **Slack** | Block Kit Approve / Reject; HMAC-SHA256 on interactions (5-minute replay window). Channel picked via Slack OAuth. Manifest in `slack/app-manifest.yaml` declares Events API (`app_uninstalled`, `tokens_revoked`) and history scopes. |
| **Email** | SMTP (Brevo-shaped) for invites, fix-proposed, and signed approve/reject links (`/api/email/action`). |

---

## Dashboard

Cookie session (`access_token` / rotating `refresh_token`). Nginx proxies
`/api/` and `/ws/` to the backend and `/` to Next — **no HTTP Basic Auth**.

| Route | What it is |
|-------|------------|
| `/` | Marketing landing |
| `/login`, `/signup`, `/accept-invite`, `/join` | Auth and org invites |
| `/dashboard` | Overview, clarifications, scan |
| `/issues/[id]` | Rubric, evidence, fix-review thread |
| `/repos`, `/repos/[id]/settings` | Per-repo kill switches, Cloudflare Pages project, category toggles, thresholds, Ask Mode |
| `/connect` | GitHub OAuth + connect repos |
| `/org` | Members, designations, routing, GitHub App installation id |
| `/activity` | WebSocket `/ws/activity` |
| `/profile` | Account, Slack/GitHub disconnect |
| `/admin`, `/admin/users`, `/admin/orgs` | Platform admin, including the global detection/proposals kill switch |

Counsel lives in the sidebar (`/api/counsel/ask` SSE; heavy jobs on the
worker).

---

## Repository layout

```
backend/
  app/
    graphs/           bug, fix, approval, outcome_checker
    detectors/        one module per CATEGORY_REGISTRY key
    counsel/          read-only analyst + PRD/investigate jobs
    integrations/     github (+ App auth), slack, cloudflare, email, context7
    sandbox/          bare mirror + worktrees, docker_runner
    routers/          REST, webhooks, WS, auth, org, counsel, fix_review
    worker.py         privileged queue consumer
    hybrid_retrieval.py, graph_index.py, embeddings.py, memory_traces.py
    prompts.py, prompt_compiler.py, azure_client.py
    enums.py          the one Issue/Fix status machine every surface renders
  alembic/            additive migrations (create_all + schema_sync still boot)
  tests/              pytest modules under backend/tests
frontend/             Next.js App Router dashboard
nginx/                vhost actually installed on the demo host
slack/                Slack app manifest
plan.md               architecture essay (partly stale vs this file)
```

`workspace/` and `workspace.*/` are gitignored. They are live checkouts, not
source. Do not commit them — git remotes inside those mirrors have leaked
PATs before.

---

## Setup

### Prerequisites

- Docker + Docker Compose
- Azure OpenAI: fast / worker / planner deployments, plus an embedding
  deployment
- GitHub: a PAT **or** (preferred) a GitHub App (`contents`, `issues`,
  `pull_requests`) plus optional OAuth App for “Connect”
- Cloudflare Pages project + API token (`Account / Cloudflare Pages / Edit`)
- Slack app from [`slack/app-manifest.yaml`](./slack/app-manifest.yaml)
- SMTP if you want invites and email approval

### Configure

Copy [`.env.example`](./.env.example) to `.env`. Required for a real run:

```
AZURE_API_ENDPOINT=
AZURE_API_KEY=
AZURE_OPENAI_API_VERSION=2025-04-01-preview
AZURE_FAST_DEPLOYMENT=
AZURE_WORKER_DEPLOYMENT=
AZURE_PLANNER_DEPLOYMENT=
AZURE_EMBEDDING_DEPLOYMENT=text-embedding-3-small

CLOUDFLARE_ACCOUNT_ID=
CLOUDFLARE_API_TOKEN=
CLOUDFLARE_PAGES_PROJECT=

GITHUB_TOKEN=          # PAT fallback; App below is preferred
GITHUB_APP_ID=
GITHUB_APP_PRIVATE_KEY=
GITHUB_APP_INSTALLATION_ID=
GITHUB_WEBHOOK_SECRET=

ADMIN_PASSWORD=        # do not leave the code default
SESSION_SECRET=        # do not leave "change-me-in-real-deployments"
NOTIFY_EMAIL=
PUBLIC_BASE_URL=https://whip-guard.zakarias.in
```

Optional: `SLACK_*`, `SMTP_*` / `EMAIL_FROM_*`, GitHub/Slack OAuth client
ids, `WORKSPACE_HOST_ROOT` (put clones on a data disk; Compose defaults to
`./workspace`).

Per-category thresholds live on the repo row and in `CATEGORY_REGISTRY`.
`ASSURANCE_THRESHOLD` / `RESOLUTION_THRESHOLD` in `.env` are legacy
fallbacks, not the sliders on `/repos/[id]/settings`.

### Run

```bash
docker compose up --build
```

- Dashboard: http://localhost:3300
- API: http://localhost:8300 (`/healthz`, `/api/*`, `/ws/activity`)

The **worker** is what mounts `/var/run/docker.sock` and translates
container paths to `WORKSPACE_HOST_PATH` before starting sandbox siblings.
The API container does not get the socket.

### Tests

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest tests/ -v
```

270 tests across 33 modules. Graph tests mock Azure and detectors; mechanical
invariants (no merge method, outcome-check fail-closed, poller idempotency,
org isolation, prefix byte-identity, webhook HMAC, Fix Council retry) are asserted directly.

---

## How reliability is tested

1. **Scores are rubrics** traced to `CouncilRun` rows.
2. **Mechanical recheck** of the same detector before a second model call;
   a flake zeroes the score in code.
3. **Approved artifact is the shipped artifact**, with a freshness check if
   the base branch moved.
4. **Post-deploy oracle** re-runs the category detector against the live
   preview URL, not a status-code ping.
5. **Outcome checker fails closed** on cross-app disagreement
   (`test_stale_slack_message_fails_closed_even_though_three_of_four_are_fine`).
   An unconfigured Slack is not treated as disagreement.
6. **No merge capability** on `github_client`
   (`test_no_merge_capability_exists_on_the_client`).
7. **A below-threshold fix never pushes or opens a PR** (`test_runner.py`).
8. **Poller is idempotent per `(repo_id, issue number)`** and swallows one
   GitHub error without killing the loop.
9. **Write-scope** is enforced in `write_file`, not in a prompt.

A seeded UI bug in the fixture repo (`app.js` delete handler) has been run
live: real GitHub issue, real draft PR, real Pages preview, same Playwright
assertion against that URL.

---

## Security & scope

- **Auth**: session cookies; `/api/webhooks/*`, Slack interactions, Slack
  Events, email action links, and `/healthz` are public (they carry their
  own signatures or are called by GitHub/Slack). GitHub webhooks are
  verified with `X-Hub-Signature-256` when `GITHUB_WEBHOOK_SECRET` is set.
  Delivery ids are stored in `webhook_deliveries` (and still in the
  process-local set).
- **Worker holds the Docker socket**; the API process that renders
  untrusted repo and model text does not. Repo code still runs in a
  throwaway `mcr.microsoft.com/playwright` container (non-root,
  `SANDBOX_UID`/`GID`), never on the orchestrator host. The worker also
  bind-mounts `${PWD}:${PWD}:ro` so in-container `docker compose` redeploy
  resolves on the host daemon.
- **Slack interactions** are HMAC-verified. Email actions are signed,
  single-use, expiring tokens.
- **Secrets must not be logged**, including in evidence and GitHub issue
  bodies (security detector attaches pattern labels, not matched values).
  gitleaks runs when the binary is on PATH; GitHub Dependabot alerts are
  merged into the security category without replacing the regex scan.
- **Kill switches**: per-repo on `/repos/[id]/settings`, and a global pair
  on `/admin`. The worker and the webhook handler both honour them.
- Set `ADMIN_PASSWORD` and `SESSION_SECRET`. The process **refuses to boot**
  on the demo defaults unless `ALLOW_INSECURE_DEFAULTS=true` (tests only).

---

## Installable app and notifications

WhipGuard installs to a home screen: `display: standalone`, maskable icons, an offline
shell, and a service worker that caches the static shell and **never** `/api/` — a cached
verdict presented as a current one is worse than no verdict.

Push arrives when a bug is raised, when a fix is ready for review, and when a verification
fails. Delivery is addressed through `repos.org_id`, so a finding reaches everyone in the
owning organization rather than whoever happened to trigger the scan, and it sits inside the
same `should_notify` guard as the email and Slack paths — a flapping condition that is not
worth an email is not worth a phone buzzing.

Turn it on per device in **Profile → Notifications**. The toggle requests permission itself,
so nobody has to find it in browser settings, and "blocked" is shown as its own state
because no browser lets a blocked site re-prompt from the page. **Send a test notification**
is the only thing that proves the whole path: a push service's `201` proves transport, never
delivery, and a toggle reading "enabled" proves only that permission was granted.

On iOS, `PushManager` exists only for a home-screen app, never a Safari tab. The settings
panel detects that and says to install first rather than reporting "unsupported" to someone
holding a supported phone.

Icons are generated, not checked in blind: `python3 frontend/scripts/make-icons.py`.

## Known gaps

Tracked as product work, not hidden:

- Write-scope globs in `CATEGORY_REGISTRY` are still fixture-shaped; retrieval
  entry files now infer from the worktree when those defaults are missing.
- `create_all` + additive `schema_sync` still run at boot. Alembic is added
  (`backend/alembic`) as a parallel, additive path — destructive migrations
  are still not automated.
- Org admin UI still opens `orgs_for_user()[0]`; dashboard reads now union
  every membership. A person can still only *join* one org at a time.
- `ask_human` now pauses the Fix Council, but the ReAct transcript is not
  checkpointed — resume starts a fresh attempt with the answer as feedback.
- An activity event with no `repo_id` reaches nobody. That is the safe
  direction, but it means a future emitter added outside an `activity_scope`
  goes silently missing from the feed rather than failing loudly. The
  broadcaster logs each one.
- The browser's own `pushManager.subscribe()` cannot be exercised headlessly — Chromium
  has no push service connection and fails regardless of permission — so that one hop is
  verified on a real device via the test button, not in CI.
- Do not commit `workspace/` or `workspace.*/` (PATs in `mirror/config`).
