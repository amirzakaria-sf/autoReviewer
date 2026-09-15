# WhipGuard

**An AI bug council that watches a repository, detects real UI bugs, proposes a
verified fix, and only ships after a human approves and a live re-check confirms
the fix actually holds.**

Live demo: **https://whip-guard.zakarias.in**
---

## Table of contents

1. [What this is](#what-this-is)
2. [Agentic harness — the part this is actually built around](#agentic-harness--the-part-this-is-actually-built-around)
3. [External apps this integrates with](#external-apps-this-integrates-with)
4. [Repository layout](#repository-layout)
5. [Setup](#setup)
6. [How reliability was tested](#how-reliability-was-tested)
7. [Security & scope](#security--scope)

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
deliberately *not* built (and why) lives in [`plan.md`](./plan.md). This README
covers the concepts at a practical, code-level depth; `plan.md` covers the
reasoning behind each one.

---

## Agentic harness — the part this is actually built around

### Two graphs, real state machines, not prose

`backend/app/graphs/bug_council.py` and `fix_council.py` are LangGraph
`StateGraph`s with real conditional edges, not a sequence of function calls
wearing the word "graph":

```
BugCouncilGraph:
  START -> DetectNode -> SkepticNode -> MechanicalRecheckNode -> ArbiterNode
        -> conditional(score >= 75): raise -> END | hold -> END

FixCouncilGraph:
  START -> RetrievalNode -> PatchGenerationNode -> VerifierNode -> ArbiterNode
        -> conditional(score >= 80): propose -> END | retry/hold -> END

ApprovalGraph (backend/app/graphs/approval_graph.py):
  resolve_approval() -> FreshnessCheck -> ApplyPatch -> OpenPR -> CloudflareDeploy
        -> PostDeployOracle -> conditional(passes):
             yes -> OutcomeCheckNode -> conditional(4-way agree): verified | outcome-check-failed
             no  -> verification-failed
```

`DetectNode`/`MechanicalRecheckNode` are **not model calls** — they shell into a
throwaway Docker sandbox (`app/sandbox/docker_runner.py`) and run the repo's own
Playwright suite for real. A candidate bug is a failing assertion with a real
trace attached, not a model's guess.

### The stable-prefix / volatile-suffix prompt contract

`backend/app/prompts.py` implements one rule end to end:

> A byte that repeats across calls belongs in a stable, cached prefix. A byte
> that changes this call belongs in the volatile suffix. Mixing the two
> forfeits the provider's prompt cache for every remaining call in the attempt.

- `build_prefix(role, workspace_map, category_rules)` — deterministic; the same
  inputs produce byte-identical output (`tests/test_prompts.py::test_identical_inputs_produce_byte_identical_prefix`).
- `pad_to_cache_floor(prefix)` — most providers won't cache a prefix under
  ~1024 tokens. A short persona-only prefix gets padded by repeating its own
  static content (never per-attempt content) until it clears the floor.
- `partition_key(repo, role, prefix)` — `sha256(prefix)[:16]`-based cache
  partitioning, so two parallel attempts with the same persona/rules share one
  cache partition, and a rules change gets a fresh key instead of colliding.
- **The retry contract**: on a rejected patch, the stable prefix passed to
  `PatchGenerationNode`'s second call is **byte-identical** to the first — only
  the volatile suffix gains the jury's rejection reason
  (`tests/test_fix_council_graph.py::test_retry_stable_prefix_is_byte_identical_across_attempts`).
  A prefix that drifts even by whitespace between attempt 1 and 2 forfeits the
  cache benefit *and* makes it impossible to know whether a behavior change
  came from the feedback or an accidental rewording underneath it.
- **The workspace map is computed by code, in `app/workspace_map.py`, not
  discovered by tool calls.** A model told "figure out the stack" spends
  several tool round-trips on a question a handful of pre-computed facts
  answer for free. This is stated as the single highest-leverage token
  optimization in `plan.md` §9.4.
- `tests/test_prompts.py::test_oversized_rules_fail_not_truncate` exists
  specifically so a rules file that outgrows its configured budget **fails a
  test**, rather than silently truncating with only a log line as the signal —
  a real regression this design is built to never repeat.

### Model routing per role, not one model for everything

`backend/app/azure_client.py` routes each council seat to a different Azure
OpenAI deployment, on the reasoning that different roles have different
accuracy/cost curves:

| Role | Deployment | Why |
|---|---|---|
| Skeptic | `AZURE_FAST_DEPLOYMENT` | adversarial pattern-matching against a fixed rubric is a recall/speed job |
| Verifier / patch generation | `AZURE_WORKER_DEPLOYMENT` | coding-agent seat — cost of a wrong patch outweighs the tier cost delta |
| Arbiter | `AZURE_PLANNER_DEPLOYMENT` | the one seat whose structured score is trusted without a second check |

### Structured output, not parsed prose

The Arbiter's response is validated Pydantic (`ArbiterVerdict` in
`azure_client.py`): `score`, a list of `{factor, weight, note}` line items that
sum to the score, a one-sentence verdict, and an optional
`needs_clarification`. This is what makes the dashboard's rubric view real
data traced to a `CouncilRun` row, not a model's paragraph that has to be
parsed and hoped about (`app/models.py::CouncilRun`).

### The ReAct loop, bounded, with a loop tripwire

`PatchGenerationNode` (`fix_council.py`) drives a real tool-calling loop
(`read_file` / `write_file` / `finish_patch`) against Azure OpenAI's function
calling, capped at `MAX_TOOL_ITERATIONS = 8`. Every tool call is fingerprinted
(`name` + canonicalized args); a repeat streak past `REPEAT_NUDGE_THRESHOLD=3`
gets an explicit "that will not produce a different result" nudge, and past
`REPEAT_FAIL_THRESHOLD=5` the attempt fails outright rather than spinning.
`write_file` is refused for any path outside the category's scope glob
(`is_in_scope()`), enforced in code — not by asking the model nicely.

### Retrieval — scoped, not "dump the file"

`RetrievalNode` does a one-hop static import scan (`re.finditer` over
`import`/`require` statements, plus a reverse scan for files that import the
touched file back) — no vector database, no graph database. `plan.md` §16
names this as a deliberate cut: the actual retrieval need here ("the touched
file plus what it imports/is imported by") is fully answered by a static scan;
a vector/graph store answers a retrieval question this demo doesn't have.

### Human-in-the-loop as an interrupt, not a poll

`resolve_approval(fix_id, approved, actor, surface)` is the **one** function
every surface calls — a Slack button click (signature-verified in
`routers/webhooks.py` before anything is trusted), a dashboard click, or a
GitHub `/reject` issue comment. Whichever surface acts first wins; a second
call against an already-resolved fix returns `already_handled` instead of
double-applying anything. This is the same convergence principle applied to
*where an approval comes from* that plan.md applies elsewhere to *where a fix
request comes from* (see "resolving bugs raised by others," below).

### The cross-app outcome checker — the actual reliability mechanism

`backend/app/graphs/outcome_checker.py`'s `check_outcome()` is intentionally
small and mechanical: four independent reads (GitHub issue+PR state, a live
re-run of the gating Playwright assertion against the real Cloudflare URL, the
Slack thread's current text, the dashboard's own `Fix.status`), reduced to one
enum. **Any disagreement fails the whole run closed**, even if every step
upstream individually reported success — this is the thesis
`tests/test_outcome_checker.py::test_stale_slack_message_fails_closed_even_though_three_of_four_are_fine`
exists to prove: three systems can be completely fine and the run still isn't
trusted if the fourth disagrees.

### Notification dedupe — not send-on-every-event

`backend/app/notifications.py` keys one `Notification` row per
`(fix_or_issue_id, condition_key)` — a repeating condition updates one row's
`occurrence_count` rather than inserting a new row and sending a new message
each time. A brand-new, ordinary condition needs `min_occurrences=2`
consecutive sightings before it ever pages anyone (a single flaky detection
shouldn't). An **escalation** (`is_escalation=True`) bypasses that
unconditionally — used for a confirmed raised bug (already past the assurance
threshold and a mechanical recheck, and identified by a fresh UUID with no
"second occurrence" to wait for), a verification failure, and an outcome-check
mismatch. All four properties are asserted directly in
`tests/test_notifications.py`.

### Resolving bugs this system didn't raise itself, in parallel

`Issue.origin` is `detected` or `filed-externally`. `backend/app/poller.py`
polls GitHub every 30s for any open issue labeled `whipguard:fix-me` that
isn't already tracked, creates the `Issue` row, and fires the exact same
`trigger_fix_council()` (`app/runner.py`) a Bug-Council-raised issue would —
the Fix Council does not care who or what decided a fix is worth attempting,
only that a matching issue exists. Every `trigger_fix_council()` call is
scheduled as an independent `asyncio.create_task`, each in its own git
worktree (`app/sandbox/worktree.py`) and its own throwaway sandbox container —
nothing serializes one fix behind another, so several run concurrently by
construction, not as an afterthought bolted on later.

### Fails closed, everywhere

- A tool/API error stops the pipeline before the next irreversible step —
  never silently retried into a different path, never treated as an implicit
  pass (`plan.md` §12).
- `hasattr(github_client, "merge_pr")` is asserted `False` directly in
  `tests/test_github_client.py` — the forbidden action (never merge, only ever
  open a PR) is an *absent capability* in the client, not a flag or a prompt
  instruction that could be argued around.
- The approved artifact is the shipped artifact: the exact patch on disk at
  approval time is pushed — never regenerated — with a freshness re-check
  (`git merge-base --is-ancestor`) if the base branch moved underneath it.

---

## External apps this integrates with

| App | What it's used for |
|---|---|
| **GitHub** | Raises a labeled issue, opens a draft PR (`Closes #N`), never merges — `merge` is not a method that exists on the client, not a flag that defaults off. A registered webhook (`push`, `issue_comment`) drives real detection-on-push and `/reject`-via-comment, deduped on `X-GitHub-Delivery` so a redelivery can't double-fire anything. |
| **Cloudflare Pages** | Real per-branch preview deploy (`wrangler pages deploy --branch`), the artifact a judge can actually click and load — a fresh Pages project, not a mocked URL. |
| **Slack** | Block Kit interactive message with Approve/Reject buttons (`slack_client.build_fix_proposed_blocks`), signature-verified via HMAC-SHA256 before any click is trusted (`verify_signature`, with a 5-minute replay window), the thread's text kept in sync with the fix's current status so the outcome checker has something real to read back. |
| **Azure OpenAI** | Model backend for the Skeptic / Verifier / Arbiter / patch-generation roles, routed per-role to a fast vs. a strong deployment (see above). |

Everything above is on the *acting* side, not the *reading* side: WhipGuard
writes a branch and a PR on GitHub, deploys real code to a real URL, and posts
to Slack and acts on the click — each step gated by a threshold or a human that
can refuse to proceed.

---

## Repository layout

```
backend/
  app/
    graphs/          BugCouncilGraph, FixCouncilGraph, ApprovalGraph, outcome_checker
    integrations/     github_client, cloudflare_client, slack_client (each's real contract)
    sandbox/          worktree.py (bare mirror + per-fix git worktrees), docker_runner.py
    routers/          REST API, GitHub/Slack webhooks, the /ws/activity websocket
    prompts.py        stable-prefix / volatile-suffix / cache-floor / partition-key
    azure_client.py   role-routed model calls + the ArbiterVerdict schema
    poller.py         picks up externally-filed whipguard:fix-me issues
    runner.py         fire-and-forget Fix Council trigger (concurrency by construction)
    notifications.py  condition-keyed dedupe
    enums.py          the ONE definition of Issue/Fix status, rendered everywhere
  tests/              43 tests — see "How reliability was tested" below
frontend/   Next.js dashboard (overview, issues/fixes feed, detail + rubric + outcome check, live activity)
nginx/      The vhost + upstream config actually installed on the demo host
slack/      The Slack app manifest used to create the bot
plan.md     Full design doc — read this for the "why" behind every decision
```

---

## Setup

### Prerequisites
- Docker + Docker Compose
- An Azure OpenAI resource with four deployments (fast / worker / planner / mechanical)
- A GitHub personal access token scoped to one repo (`contents:write`, `issues:write`, `pull_requests:write`)
- A Cloudflare account with a Pages project + an API token (`Account / Cloudflare Pages / Edit`)
- A Slack app created from [`slack/app-manifest.yaml`](./slack/app-manifest.yaml), installed to a workspace

### Configure

Copy the values into a `.env` file at the repo root (see `.env.example`):

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

The backend container mounts the host's Docker socket to run sandboxed fix
attempts as sibling containers ("Docker outside of Docker") — see
[Security & scope](#security--scope) for the trade-off this makes, and
`WORKSPACE_HOST_PATH`/`SANDBOX_UID`/`SANDBOX_GID` in `docker-compose.yml` for
why a bind-mount path has to be translated to the *host's* path before it's
handed to the host's own Docker daemon.

### Run the backend test suite

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest tests/ -v
```

---

## How reliability was tested

This is the part the design is actually built around — see `plan.md` §11–§12
for the full reasoning. In short:

1. **Every score is a rubric, not a bare number.** Each Skeptic/Verifier/Arbiter
   call is recorded as a `CouncilRun` row; the dashboard's rubric view is real
   data traced to those rows, not a model's prose.
2. **A mechanical, re-runnable check gates every score.** The Bug Council
   re-runs the *same* Playwright spec, right now, before the Arbiter ever scores
   it a second time — a flake that passes on rerun is dropped in code
   (`tests/test_bug_council_graph.py::test_mechanical_recheck_flake_zeroes_the_score_before_any_second_model_call`).
3. **The approved artifact is the shipped artifact.** The exact patch shown at
   approval time is applied — never regenerated — with a freshness re-check if
   the base branch moved underneath it.
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
7. **A rejected fix never touches GitHub**, tested directly
   (`tests/test_runner.py::test_trigger_fix_council_below_threshold_rejects_and_never_pushes_or_opens_pr`)
   — `push_branch`/`create_draft_pr` are asserted `not_called`, not just "should
   not happen by the code's shape."
8. **The externally-filed-bug poller is idempotent by construction**
   (`tests/test_poller.py::test_poll_once_does_not_duplicate_or_retrigger_an_already_tracked_issue`)
   and fails one polling cycle closed without killing the loop
   (`test_poll_once_swallows_github_api_errors_without_propagating`).
9. **43 automated tests** cover the stable-prefix/cache-floor prompt contract,
   the retry-byte-identical-prefix guarantee, notification dedupe (first
   sighting never pages an ordinary condition, escalations bypass that), and
   the routing logic for both councils — run with `pytest backend/tests/ -v`.

Every seeded-bug run was also verified live end-to-end against a real fixture
repo (a deliberately introduced off-by-one bug in `app.js`'s delete handler),
not mocked: a real GitHub issue was raised, a real draft PR opened with a
working diff, a real Cloudflare Pages deploy created, and the exact same
Playwright assertion re-run against that live URL — confirmed passing.

---

## Security & scope

- **No application-level login was built for this Tier-0 scope** — protected
  at the nginx layer with HTTP Basic Auth instead (credentials above), with the
  GitHub webhook and Slack interactions endpoints explicitly exempted (they're
  called by GitHub/Slack's own servers, which don't send basic-auth
  credentials, not by a logged-in browser).
- **The Docker-outside-of-Docker trade-off, named rather than hidden**: the
  backend container mounts the host's Docker socket to launch sandboxed fix
  attempts as sibling containers. `plan.md` §13's own stated rule is that this
  socket should never be mounted into the process that also renders untrusted
  repo/model content — Tier 0 runs both in one process for build-time
  simplicity, a real, named gap against a genuinely hostile repository (see
  `plan.md` §16's deliberate-cuts table), acceptable here because the
  connected repo is WhipGuard's own seeded fixture app, not adversarial input.
- **All repo code executes inside a throwaway Docker container**
  (`mcr.microsoft.com/playwright:v1.63.0-jammy`, non-root, matching the host
  user's UID/GID) — never on the orchestrator's own host.
- **Write-scope enforcement in code**: `PatchGenerationNode`'s `write_file`
  tool refuses any path outside the category's scope glob, rather than relying
  on a prompt instruction.
- **Slack payloads are signature-verified** (HMAC-SHA256 against the signing
  secret, with a timestamp replay window) before any button click is trusted.
- **Secrets are never logged.**
