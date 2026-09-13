# WhipGuard

**An AI bug council that watches a repository, detects real UI bugs, proposes a
verified fix, and only ships after a human approves and a live re-check confirms
the fix actually holds.**

Live demo: **https://whip-guard.zakarias.in**

Demo video: _[Adding it - just give me 10 mins please ]_

---

## What this is

WhipGuard runs a two-role jury (a Skeptic + mechanical evidence + an Arbiter) over
a connected repository's own Playwright suite. A finding that clears threshold
becomes a real GitHub issue. A second jury (a Verifier that actually rebuilds and
re-tests the patched branch + an Arbiter) turns that issue into a real draft pull
request. A human approves from Slack or the dashboard. On approval, WhipGuard
pushes the branch, opens the PR, deploys the branch to a real Cloudflare Pages
preview URL, and re-runs the exact same check against the *live* URL — not just
the diff. A final **cross-app outcome checker** reads GitHub, Cloudflare, Slack,
and the dashboard back independently and fails the whole run closed if any of
them disagree with each other, even if every individual step upstream reported
success.

Full architecture, the reasoning behind every design choice, and what's
deliberately *not* built (and why) lives in [`plan.md`](./plan.md).

## External apps this integrates with

| App | What it's used for |
|---|---|
| **GitHub** | Raises a labeled issue, opens a draft PR (`Closes #N`), never merges — `merge` is not a method that exists on the client, not a flag that defaults off. |
| **Cloudflare Pages** | Real per-branch preview deploy (`wrangler pages deploy`), the artifact a judge can actually click and load. |
| **Slack** | Block Kit interactive message with Approve/Reject buttons, signature-verified before any click is trusted. |
| **Azure OpenAI** | Model backend for the Skeptic / Verifier / Arbiter / patch-generation roles, routed per-role to a fast vs. a strong deployment. |

Everything above is on the *acting* side, not the *reading* side: WhipGuard
writes a branch and a PR on GitHub, deploys real code to a real URL, and posts
to Slack and acts on the click — each step gated by a threshold or a human that
can refuse to proceed.

## Repository layout

```
backend/    FastAPI + LangGraph (Bug Council, Fix Council, approval, outcome checker)
frontend/   Next.js dashboard (overview, issues/fixes feed, detail + rubric, live activity)
nginx/      The vhost + upstream config actually installed on the demo host
slack/      The Slack app manifest used to create the bot
plan.md     Full design doc — read this for the "why" behind every decision
```

## Setup

### Prerequisites
- Docker + Docker Compose
- An Azure OpenAI resource with four deployments (fast / worker / planner / mechanical)
- A GitHub personal access token scoped to one repo (`contents:write`, `issues:write`, `pull_requests:write`)
- A Cloudflare account with a Pages project + an API token (`Account / Cloudflare Pages / Edit`)
- A Slack app created from [`slack/app-manifest.yaml`](./slack/app-manifest.yaml), installed to a workspace

### Configure

Copy the values into a `.env` file at the repo root:

```
DATABASE_URL=postgresql+asyncpg://whipguard:whipguard@postgres:5432/whipguard

AZURE_API_ENDPOINT=...
AZURE_API_KEY=...
AZURE_OPENAI_API_VERSION=...
AZURE_FAST_DEPLOYMENT=...
AZURE_WORKER_DEPLOYMENT=...
AZURE_PLANNER_DEPLOYMENT=...
AZURE_MECHANICAL_DEPLOYMENT=...

CLOUDFLARE_ACCOUNT_ID=...
CLOUDFLARE_API_TOKEN=...
CLOUDFLARE_PAGES_PROJECT=...

SLACK_BOT_TOKEN=xoxb-...
SLACK_SIGNING_SECRET=...
SLACK_CHANNEL_ID=...

GITHUB_TOKEN=...
FIXTURE_REPO=owner/repo

ASSURANCE_THRESHOLD=75
RESOLUTION_THRESHOLD=80
```

### Run

```bash
docker compose up --build
```

- Dashboard: http://localhost:3300
- API: http://localhost:8300 (`/healthz`, `/api/*`, `/ws/activity`)

### Run the backend test suite

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest tests/ -v
```

## How reliability was tested

This is the part the design is actually built around — see `plan.md` §11–§12 for
the full reasoning. In short:

1. **Every score is a rubric, not a bare number.** Each Skeptic/Verifier/Arbiter
   call is recorded as a `CouncilRun` row; the dashboard's rubric view is real
   data traced to those rows, not a model's prose.
2. **A mechanical, re-runnable check gates every score.** The Bug Council
   re-runs the *same* Playwright spec, right now, before the Arbiter ever scores
   it a second time — a flake that passes on rerun is dropped in code
   (`tests/test_bug_council_graph.py::test_mechanical_recheck_flake_zeroes_the_score_before_any_second_model_call`).
3. **The approved artifact is the shipped artifact.** The exact hash-pinned
   patch shown at approval time is applied — never regenerated — with a
   freshness re-check if the base branch moved underneath it.
4. **Verification happens against the live deployment, not just the diff.**
   The post-deploy oracle re-runs the same assertion against the real
   Cloudflare URL, not a status-code check.
5. **A cross-app outcome checker fails closed on disagreement.** GitHub,
   Cloudflare, Slack, and the dashboard are read back independently after the
   run and reduced to one status; any mismatch — even a stale Slack message
   while the other three are fine — is reported as `outcome-check-failed`
   (`tests/test_outcome_checker.py::test_stale_slack_message_fails_closed_even_though_three_of_four_are_fine`).
6. **No `merge` capability exists on the GitHub client**, tested directly
   (`tests/test_github_client.py::test_no_merge_capability_exists_on_the_client`),
   not just documented as a rule.
7. **33 automated tests** cover the stable-prefix/cache-floor prompt contract,
   the retry-byte-identical-prefix guarantee, notification dedupe (first
   sighting never pages, escalations bypass cooldown), and the routing logic
   for both councils — run with `pytest backend/tests/ -v`.

Every seeded-bug run was also verified live end-to-end against a real fixture
repo (a deliberately introduced off-by-one bug), not mocked: a real GitHub
issue was raised, a real draft PR opened with a working diff, and a real
Cloudflare deploy re-checked against the live URL.
