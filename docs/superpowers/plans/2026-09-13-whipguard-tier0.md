# WhipGuard Tier 0 — Implementation Plan (lightweight, time-boxed)

> Execution mode: inline, direct build (not subagent-driven-development) — ~3hr hackathon
> window, agreed with user 2026-09-13. Tests written where they carry real signal
> (enums/outcome-checker/notification-dedupe/mechanical evidence); everything else verified
> by running the real end-to-end demo path, not by exhaustive TDD ceremony.

**Goal:** Ship the Tier 0 slice of `plan.md`: one seeded UI bug, detected by a 2-role Bug
Council, fixed by a Fix Council, approved via Slack or dashboard, deployed to Cloudflare
Pages, re-verified live, and cross-checked across GitHub/Cloudflare/Slack/dashboard by the
outcome checker — end to end, clickable by a judge.

**Architecture:** FastAPI + LangGraph (Postgres-checkpointed) backend on :8300, Next.js
dashboard on :3300, Postgres 16 (plain, no pgvector), Docker sandbox per fix worktree. Reuses
Whip's Azure OpenAI deployments and Cloudflare account. Reuses the host's stored git
credential for GitHub. Fresh Slack app via manifest.

**Tech stack:** Python 3.12, FastAPI, LangGraph + `langgraph-checkpoint-postgres`,
SQLAlchemy (async) + asyncpg, Playwright (Python), httpx (GitHub/Slack/Cloudflare REST),
Next.js 15 (App Router) + Tailwind, Postgres 16, Docker.

**Spec:** `../../../plan.md` (this plan implements §2–§17 of that document, Tier 0 scope only)

## Global constraints (from plan.md — copied verbatim where it matters)

- Forbidden actions, enforced in code, not prompt instruction: no `merge` method exists on
  the GitHub client at all; no force-push; no branch delete except a fix's own after
  retention; no regenerating a patch after human approval (§1, §12).
- `Issue.status` / `Fix.status`: one enum, defined once in `app/enums.py`, rendered
  everywhere (labels, dashboard, Slack) — never re-derived (§5.1).
- Stable prefix / volatile suffix split enforced for every model call; stable prefix passed
  byte-identical across a retry (§9.1, §9.6).
- Fails closed: any GitHub/Cloudflare/Slack/Playwright call that errors stops the pipeline
  before the next irreversible step, recorded as `error` — never silently retried into a
  different path, never treated as pass (§12).
- Skip ≠ pass: a check that couldn't run is `not_checked`, visibly distinct from `passed`,
  everywhere it's shown (§12).
- Write-scope: a fix may only touch files under the `ui` category's glob (frontend files) —
  enforced by refusing the write in code (§13).
- All repo code executes inside a throwaway Docker container, never on the orchestrator host
  (§13).
- Slack payloads signature-verified before anything is trusted (§6.3, §13).
- One `Notification` row per `(fix_or_issue_id, condition_key)`, not one per detection
  (§6.4).
- Ports: backend 8300, dashboard 3300 (already reserved, don't collide with 3000/8000,
  3100/8100, 3200/8388).

---

## Task 1 — Fixture repo: `whipguard-demo-ui`

**What:** small static app (plain HTML/CSS/vanilla JS — no build step needed for a fast
Cloudflare Pages deploy) with a todo-list-style UI: add item, delete item. Seed one real bug:
the delete handler removes the item at `index - 1` instead of `index` (classic off-by-one)
when more than one item exists. Write a Playwright spec (`tests/delete.spec.ts`) that adds
three items, deletes the middle one, and asserts the correct two remain — this spec fails
against the seeded bug and passes once fixed.

**Files (new repo, pushed to GitHub under the user's account, name `whipguard-demo-ui`):**
- `index.html`, `style.css`, `app.js` (the bug lives in `app.js`'s delete handler)
- `playwright.config.ts`
- `tests/delete.spec.ts`
- `package.json` (playwright + a trivial `serve` dev dependency)
- `README.md` (one line: "Seeded-bug fixture app for WhipGuard demos")

**Test:** `npx playwright test` locally against the repo, confirm it FAILS on the seeded bug,
then hand-fix locally to confirm the same spec PASSES — then revert the hand-fix before
pushing (the bug must ship seeded). Push to GitHub via the stored credential.

**Done when:** repo exists on GitHub, CI-free, `npx playwright test` fails deterministically
on `main`.

---

## Task 2 — Backend skeleton + Postgres schema

**Files:**
- `backend/pyproject.toml` (fastapi, uvicorn, langgraph, langgraph-checkpoint-postgres,
  sqlalchemy[asyncio], asyncpg, httpx, playwright, python-dotenv, pydantic-settings)
- `backend/app/config.py` — `Settings(BaseSettings)`: reads `AZURE_*`, `CLOUDFLARE_*`,
  `SLACK_*`, `GITHUB_*`, `DATABASE_URL`, `FIXTURE_REPO` from env.
- `backend/app/enums.py` — `IssueStatus`, `FixStatus` (str, Enum) — single source, matches
  plan.md §5.1's list plus `outcome-check-failed`.
- `backend/app/db.py` — async SQLAlchemy engine/session/`Base`.
- `backend/app/models.py` — `Repo, Issue, Fix, CouncilRun, Notification, AuditLog,
  OutcomeCheck` per plan.md §5 (SQLAlchemy ORM classes).
- `backend/app/main.py` — FastAPI app, `Base.metadata.create_all` on startup (no Alembic —
  hackathon speed, single environment, acceptable per time-box).
- `backend/Dockerfile`, `backend/.dockerignore`
- `backend/tests/test_enums.py` — asserts every `FixStatus`/`IssueStatus` value maps to
  exactly one GitHub label string and one dashboard badge string via a single lookup table
  (the anti-drift test plan.md §5.1 asks for).

**Test:** `pytest backend/tests/test_enums.py -v` passes; `docker compose up postgres backend`
boots and `/healthz` returns 200.

---

## Task 3 — Prompt builder (stable prefix / volatile suffix / cache floor)

**Files:**
- `backend/app/prompts.py` — `build_prefix(role, workspace_map, category_rules) -> str`,
  `pad_to_cache_floor(prefix, floor_tokens=1024) -> str` (repeats already-static prefix
  content, never per-attempt content), `partition_key(repo, role, prefix) ->
  "{repo}:{role}:{sha256(prefix)[:16]}"`.
- `backend/tests/test_prompts.py` — asserts: (a) two calls with identical role+workspace_map
  produce byte-identical prefixes (retry contract, §9.6); (b) a short persona-only prefix
  gets padded to >= 1024 tokens; (c) `partition_key` changes when `prefix` changes and stays
  stable when it doesn't; (d) a rules blob larger than the configured budget FAILS the test
  rather than silently truncating (the exact regression named in plan.md §9.4).

**Test:** `pytest backend/tests/test_prompts.py -v` — all pass.

---

## Task 4 — Azure OpenAI client (role-routed)

**Files:**
- `backend/app/azure_client.py` — thin wrapper over `openai.AzureOpenAI`, one function per
  role: `call_skeptic(prefix, suffix) -> str`, `call_verifier(...)`, `call_arbiter(prefix,
  suffix) -> ArbiterVerdict` (Pydantic model: `score: int, factors: list[{factor, weight,
  note}], verdict: str, needs_clarification: dict | None`), `call_patch_worker(...)`. Reads
  `AZURE_FAST_DEPLOYMENT` (Skeptic), `AZURE_WORKER_DEPLOYMENT` (Verifier + patch-gen),
  `AZURE_PLANNER_DEPLOYMENT` (Arbiter) from `Settings`.
- `backend/tests/test_azure_client.py` — mocks the Azure SDK client, asserts each role
  function calls the deployment plan.md §10 maps it to (this is the "drive it once against
  the real target, inspect the actual response" step from §9.8 — do this manually once
  outside the test suite too, against a trivial "say OK" prompt, before trusting it further).

**Test:** mocked unit test passes; one manual live call per role confirmed working (paste the
raw response once, don't just check "no exception" — per §9.8's named lesson).

---

## Task 5 — GitHub client (no merge method exists)

**Files:**
- `backend/app/integrations/github_client.py` — `create_issue(repo, title, body, labels) ->
  int`, `create_draft_pr(repo, branch, title, body, labels) -> int`,
  `comment_issue(repo, issue_number, body)`, `get_issue(repo, number)`, `get_pr(repo,
  number)`, `push_branch(...)`. **No `merge_pr` function is defined anywhere in this module —
  not a flag, an absent capability**, per plan.md §1's forbidden-action rule.
- `backend/tests/test_github_client.py` — mocks httpx, asserts request shape (labels array,
  auth header) for `create_issue`/`create_draft_pr`; asserts `hasattr(github_client,
  "merge_pr")` is `False` (a literal test for the forbidden-capability rule, not just a
  comment saying so).

**Test:** `pytest backend/tests/test_github_client.py -v`; one live call creating a throwaway
test issue on `whipguard-demo-ui`, confirmed in the GitHub UI, then closed manually.

---

## Task 6 — Cloudflare client

**Files:**
- `backend/app/integrations/cloudflare_client.py` — `deploy_branch(worktree_path,
  project_name, branch) -> preview_url` (shells out to `wrangler pages deploy --branch
  <branch>`), `get_deployment_status(project_name, branch) -> dict`.
- Manual test: run `deploy_branch` once against the fixture repo's `main` branch, confirm a
  real `*.pages.dev` URL responds.

---

## Task 7 — Slack client (signature-verified)

**Files:**
- `backend/app/integrations/slack_client.py` — `post_message(channel, blocks) -> ts`,
  `update_message(channel, ts, blocks)`, `verify_signature(headers, body, signing_secret) ->
  bool` (per Slack's documented HMAC scheme).
- `backend/tests/test_slack_client.py` — a known-good signature fixture (computed by hand
  against a fixed secret+body+timestamp) verifies true; a tampered body verifies false; a
  stale timestamp (>5 min) verifies false regardless of signature.

**Test:** `pytest backend/tests/test_slack_client.py -v`; once the Slack app manifest is
installed (user action, §"What I need from you"), one live `post_message` to the configured
channel, confirmed visually.

---

## Task 8 — Sandbox: worktree + Docker runner

**Files:**
- `backend/app/sandbox/worktree.py` — `create_worktree(mirror_path, issue_number, slug) ->
  Path`, `remove_worktree(path)`.
- `backend/app/sandbox/docker_runner.py` — `run_in_sandbox(worktree_path, command: list[str])
  -> (exit_code, stdout, stderr)`, using a Playwright-ready base image
  (`mcr.microsoft.com/playwright:v1.48.0-jammy`), no network beyond npm/pip install, volume-
  mounts only that one worktree directory (write-scope enforcement per plan.md §13: the mount
  itself is the boundary, not a promise).

**Test:** manual — run a trivial `echo hello` and a real `npx playwright test` inside the
sandbox against a checked-out copy of `whipguard-demo-ui`, confirm the seeded bug's spec
fails inside the container exactly as it does locally.

---

## Task 9 — Bug Council graph

**Files:**
- `backend/app/graphs/bug_council.py` — `DetectNode` (runs the fixture repo's Playwright
  suite in the sandbox, packages a failing assertion as `{trace, screenshot, assertion_text}`
  evidence), `SkepticNode` (calls `azure_client.call_skeptic`), `MechanicalRecheckNode`
  (re-runs the same spec in a fresh sandbox invocation — not a cache hit), `ArbiterNode`
  (calls `call_arbiter`, writes a `CouncilRun` row per call). Wired as a LangGraph
  `StateGraph`, checkpointed via `PostgresSaver`.
- `backend/tests/test_bug_council_graph.py` — with `DetectNode`/`SkepticNode`/`ArbiterNode`
  mocked to fixed outputs, asserts the conditional edge routes to `RaiseIssueNode` at
  score>=75 and to `HoldNode` below it (the actual routing logic, not the model calls).

**Test:** the mocked routing test passes; then one real end-to-end run against
`whipguard-demo-ui`, confirming a real GitHub issue lands with `whipguard:bug`,
`whipguard:category/ui`, `whipguard:severity/<n>` labels.

---

## Task 10 — Fix Council graph

**Files:**
- `backend/app/graphs/fix_council.py` — `RetrievalNode` (static import scan: parse the
  touched file's `import`/`require` statements plus a reverse scan for files importing it —
  no vector/graph DB per plan.md §16), `PatchGenerationNode` (bounded ReAct loop: `read_file`,
  `write_file`, `run_command` tools, frozen stable prefix from Task 3, loop tripwire per
  plan.md §9.7 — fingerprint `(tool_name, canonicalized_args)`, nudge at 3 repeats, fail at
  5), `VerifierNode` (builds the patched worktree in the sandbox, runs the full Playwright
  suite, confirms the originally-failing assertion now passes), `ArbiterNode` (resolution
  score).
- `backend/tests/test_fix_council_graph.py` — asserts the retry path (score < 80 once) passes
  the jury's rejection reason into the volatile suffix only, and that the stable prefix
  argument passed to `PatchGenerationNode`'s second call is byte-identical to the first
  (direct test of the §9.6 contract, not just a comment about it).

**Test:** mocked retry-contract test passes; one real end-to-end run producing a real draft
PR on `whipguard-demo-ui` with `whipguard:fix-proposed`, `whipguard:awaiting-approval` labels,
containing an actual working diff (manually confirm the diff fixes the off-by-one).

---

## Task 11 — Approval graph (interrupt/resume) + Cloudflare deploy + post-deploy oracle

**Files:**
- `backend/app/graphs/approval_graph.py` — `WaitForApprovalNode` (LangGraph `interrupt()`),
  `resolve_approval(fix_id, approved, actor, surface)` (the one function both Slack and
  dashboard call), `FreshnessCheckNode` (compares stored base-branch SHA to current; re-runs
  `VerifierNode` if it moved), `ApplyApprovedPatchNode` (applies the hash-pinned patch stored
  at proposal time — never regenerates), `OpenBranchAndPRNode`, `CloudflareDeployNode`,
  `PostDeployOracleNode` (same Playwright spec, run against the live `*.pages.dev` URL).
- `backend/tests/test_approval_graph.py` — asserts `resolve_approval` called twice for the
  same `fix_id` (Slack then dashboard, or vice versa) is idempotent: second call is a no-op
  that returns "already handled by <first actor>", never double-applies the patch.

**Test:** mocked idempotency test passes; live run — approve from the dashboard, watch a real
branch get pushed, a real PR open, a real Cloudflare deploy happen, the oracle re-check the
live URL and pass.

---

## Task 12 — Outcome checker (§11)

**Files:**
- `backend/app/graphs/outcome_checker.py` — `OutcomeCheckNode`: reads GitHub (issue+PR
  state, labels, `merged: false`), Cloudflare (deployment status + re-runs the Playwright
  assertion against the live URL one more time), Slack (fetches the thread's current message
  text/blocks via `conversations.history`), and the local `Fix.status` row — reduces all four
  to one status enum value and writes an `OutcomeCheck` row. Any disagreement -> writes
  `Fix.status = outcome-check-failed`.
- `backend/tests/test_outcome_checker.py` — **the most important test in this plan.** Feed it
  four synthetic states that agree -> asserts `agreed=True`. Feed it four states where Slack
  says `verified` but GitHub shows the PR still `awaiting-approval` -> asserts
  `agreed=False`, `mismatch_detail` names the Slack/GitHub pair, and `Fix.status` is forced to
  `outcome-check-failed` even though three of the four sub-checks were individually fine.

**Test:** `pytest backend/tests/test_outcome_checker.py -v`, including the deliberately-
mismatched case — this is the test to show a judge if only one test gets shown.

---

## Task 13 — Notification dedupe (§6.4)

**Files:**
- `backend/app/notifications.py` — `record_condition(fix_or_issue_id, condition_key) ->
  Notification` (upsert, increments `occurrence_count`), `should_notify(notification,
  cooldown_seconds, is_escalation) -> bool`.
- `backend/tests/test_notifications.py` — asserts: first sighting of a new condition does not
  notify (dashboard row exists, no send); a second consecutive sighting does; a still-firing
  condition inside its cooldown window does not re-notify; an escalation (`is_escalation`)
  bypasses cooldown unconditionally.

**Test:** `pytest backend/tests/test_notifications.py -v`.

---

## Task 14 — API routers + webhooks + websocket

**Files:**
- `backend/app/routers/api.py` — REST: `GET /api/repos`, `GET /api/issues`, `GET
  /api/fixes/{id}`, `POST /api/fixes/{id}/approve`, `POST /api/fixes/{id}/reject`, `GET
  /api/overview` (four counts per plan.md §7).
- `backend/app/routers/webhooks.py` — `POST /api/webhooks/github` (push -> trigger
  `BugCouncilGraph`; `issue_comment` `/reject` -> `resolve_approval`), `POST
  /api/slack/interactions` (verifies signature via Task 7, routes button clicks to
  `resolve_approval`).
- `backend/app/routers/ws.py` — `/ws/activity` broadcasting graph node-transition events
  (detection started, jury verdict, score computed, deploy in progress, oracle result,
  outcome-check result) to connected dashboard clients.

**Test:** manual — `curl` each REST endpoint against a seeded DB row; trigger the webhook
locally with a synthetic GitHub payload; connect a websocket client and confirm events stream
during a real run.

---

## Task 15 — Dashboard (Next.js)

**Files:**
- `frontend/app/page.tsx` — overview: four counts (Raised by AI, Resolved & verified,
  Awaiting approval, Failed), kill switch toggle, connect-repo entry point.
- `frontend/app/issues/page.tsx` — feed, filterable by status, Origin column
  (`detected`/`filed-externally`), live concurrency chips for in-flight fixes.
- `frontend/app/issues/[id]/page.tsx` — detail: evidence (screenshot/trace/assertion text),
  diff view, rubric (Skeptic argument, mechanical result, Arbiter's weighted factors),
  Approve/Reject buttons calling the REST API, links to the live GitHub issue/PR/Cloudflare
  URL.
- `frontend/app/activity/page.tsx` — websocket-driven live event stream.
- `frontend/lib/api.ts` — typed fetch wrappers for the Task 14 endpoints.

**Test:** manual — `npm run dev`, walk the golden path in a real browser: connect ->
see a raised issue -> open detail -> see rubric -> approve -> watch activity stream ->
see verified status -> see outcome-check result. This IS the acceptance test for this task.

---

## Task 16 — Compose, nginx, and the two live demo runs

**Files:**
- `docker-compose.yml` — postgres, backend (:8300), frontend (:3300), matching sibling
  projects' pattern (plain single-instance, no blue/green per plan.md §16).
- `.env` / `.env.example` — all vars from Task 2's `config.py`, Azure/Cloudflare values
  copied from `opencode/depsAssets/.env` (reused, not duplicated as a new secret), Slack
  values from the user's app install, GitHub via the stored git credential.
- `nginx/whip-guard.conf`, `nginx/upstream-whip-guard.conf` — already installed live
  (§ done earlier); re-verify `nginx -t` after backend/frontend containers are actually
  listening on 8300/3300.
- Run `sudo certbot --nginx -d whip-guard.zakarias.in` **only once the containers are up and
  answering** (not before — an empty backend behind a fresh cert is a worse failure mode to
  debug than a plain-HTTP 502).
- **Determinism check (plan.md §12):** reset the fixture repo to its seeded state, run the
  full pipeline twice, confirm both runs reach `verified` (or report the divergence as-is if
  they don't — never re-run silently until it looks better).

**Done when:** `https://whip-guard.zakarias.in` serves the real dashboard over TLS, and two
independent seeded-bug runs both reach the same terminal status.

---

## Self-review notes

- Every plan.md §-numbered requirement in the Tier 0 scope (§1–§17) maps to a task above;
  §16's cuts (5th category, 3rd jury role, vector/graph, email, Linear/Notion/Stripe, GitHub
  App, blue/green) are honored by absence — no task builds any of them.
- No task defines a `merge_pr`/`force_push`/`delete_branch` capability anywhere; Task 5's
  test asserts the absence directly.
- Enum consistency (`IssueStatus`/`FixStatus`) is defined once in Task 2 and only ever
  imported, never redefined, in every later task that renders status.
