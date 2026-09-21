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
- **Commits:** `6093ea89a0c488db480d77a78357c0760267678f` (`6093ea8`) — Azure Responses +
  `apply_patch`. `fbcc5b6539342cb3a7aebec5f7e0e3b0b596e059` (`fbcc5b6`) — the curated
  web-research council. `7dab810c57b083b7d6508ee3ef97c4f0df537dac` (`7dab810`) — the three
  tenancy gaps closed. `355359e849946f48f667c92a744a3366fc81ba53` (`355359e`) — three more
  found by re-auditing, in `github.py`.
- **Deployed:** **yes**, 2026-09-21 05:05 UTC. `eb4bf2f` (working tree = `main`, clean) is
  live. `create_all` created `research_findings` at boot with all 15 columns — no manual
  migration. Backend, frontend and worker all recreated; postgres untouched.

- **Shipped this session (claude):**
  - `app/azure_client.py` rewritten around `complete_turn` on the **Responses API**. Native
    reasoning alongside tools (impossible on Chat Completions with GPT-5.x, which is why the
    patch loop had never once thought), reasoning items replayed across ticks, flat tool
    schemas with `strict: false` and `$defs` preserved, forced-Pydantic structured output for
    the jury and Arbiter, `prompt_cache_key` finally sent, and usage recorded for every turn
    with the protocol and reasoning cost. `WHIPGUARD_AZURE_API=chat` is the operator hatch.
  - `fix_council.py` and `counsel/agent.py` no longer construct their own `AzureOpenAI`.
    `app/embeddings.py` stays separate on purpose.
  - `app/sandbox/apply_patch.py` — exact-anchor replace, now the preferred edit;
    `write_file` kept for creation and genuine full replaces. Both gated by the same
    `scope_excludes`, inside the primitive.
  - `app/research.py` — Gatherer (`web_search`) + Curator (attribute, score, drop), findings
    persisted to the new `research_findings` table. Wired into the PRD council via a
    per-requirement planner, and into Counsel and the patch worker as `research_web`.
  - Frontend: the Counsel markdown renderer now renders bare URLs as links and `_italics_`,
    so a research citation can actually be followed.
  - **The three tenancy gaps, closed** (`7dab810`) — and each turned out to be more than
    the one line recorded against it:
    - `counsel.py` — `_resolve_context` now requires the repo to be visible and falls back
      *within* the caller's set instead of `select(Repo).first()`. Two more holes were in
      the same file: `get_job` returned any job's full result to any authenticated caller
      (a PRD's result is another org's file paths and source excerpts), and `ask` accepted
      another user's `conversation_id`, replaying their history into the model and
      appending to their transcript. `get_conversation`'s 403 became a 404.
    - `ws.py` — the socket had **no authentication at all**: `@app.middleware("http")`
      never runs for a websocket scope and `/ws/activity` is not under `/api/` either. It
      now authenticates its own handshake and closes `4401`; events carry a `repo_id`
      stamped from an ambient per-run scope, and an unaddressed event reaches nobody.
    - `human_input.py` — both endpoints scoped through issue → repo. `answer_request` was
      the dangerous one: answering enqueues a `fix_council` work item, so unscoped it let
      one org steer another's council run. The actor now comes from the session, not the
      request body.
    - `github.py` — found by re-auditing every endpoint for a tenancy dependency rather
      than by re-reading the gap list. The connectable-repo list marked a repository
      "connected" because *some* organisation had connected it; `connect_repo` returned
      another organisation's `repo_id`; and `POST /api/github/disconnect` let any member of
      any organisation clear the **deployment-wide** GitHub token, stopping every other
      organisation's pushes and PR comments. The first two are scoped; the third is platform
      admin only. Availability, not confidentiality, which is why a leak-shaped audit missed
      it the first time.
    - Frontend: both websocket consumers refresh once on a `4401` rather than reconnecting
      into the same rejection.
  - Docs: `docs/plans/2026-09-20-azure-responses-and-apply-patch.md` (with the places live
    probing contradicted the plan marked), 11 new `DECISIONS` rows, README `Model protocol`
    and `Web research` sections, `ARCHITECTURE.md`, `CHANGELOG.md`, two more `info.md` §11
    traps.

- **Verified this session:**
  - `pytest -q` → **371 passed** (343 before the tenancy slice, 270 at session start).
  - **Post-deploy, against the live host** — every claim in this file re-checked on the real
    deployment rather than on a test double:
    - Nine API surfaces 200, including the three whose scoping changed
      (`/api/human-input`, `/api/github/repos`, `/api/counsel/jobs/{id}`).
    - **The websocket contract, end to end through nginx and the NOTIFY relay**: no cookie
      refused; valid cookie connects; an event for the caller's own repo arrives; an event
      for another repo does **not**; an unaddressed event does **not**. This was the one
      change that fails visibly, and it is the one now proved live.
    - **Counsel answered a real question** — `app.js:22` cited correctly — and both turns
      recorded `{"protocol": "responses", "reasoning_tokens": 15}`. The second turn shows
      `cached_input_tokens: 1207`. Native reasoning and the provider prompt cache are both
      working in production, neither of which was ever true before today.
  - **Each tenancy fix checked against its own pre-fix behaviour**, not just against a
    passing test: the old `broadcast` does reach the other org's socket, the old job query
    does return their PRD markdown, the old fallback does pick a repo outside the caller's
    set. The websocket tests run a real handshake through `TestClient` rather than a
    hand-built fake socket, because "does the browser's cookie actually arrive here" is the
    assumption that change rests on.
  - `npx tsc --noEmit` → clean.
  - **Live Azure probes, before writing any code:** Responses + `reasoning` + flat tools in
    one request on `gpt-5.6-terra` and `gpt-5.6-luna`; reasoning items and `reasoning_tokens`
    present on real prompts; `prompt_cache_key` accepted; built-in `web_search` returning
    `['reasoning','web_search_call','message']` with `url_citation` annotations.
  - **Live patch-loop round trip:** three real ticks against Azure replaying reasoning items,
    `function_call` and `function_call_output` — no 400. The model chose `apply_patch` over
    `write_file` unprompted. Three `council_runs` rows written with
    `{"protocol": "responses", "reasoning_tokens": N}`, then removed.
  - **Live research run:** Express 5's `app.del` removal. Two searches, one source returned,
    four claims kept and attributed to it, rows persisted to `whipguard_test` and cleaned up.

### Needs verification

- **A full fixture Fix Council run under the new protocol** — detect → patch → verify →
  propose. The patch loop is proved live (three ticks against Azure, reasoning replayed, no
  400) and Counsel is proved live end to end, but the Fix Council graph has not been re-run
  since the protocol change. It opens a PR on the fixture repo, so it needs the user to ask.
- `GITHUB_WEBHOOK_SECRET` is **unset on both sides** — absent from `.env`, and GitHub's own
  hook (id `678747728`) reports `"secret_set": false`. The signature check shipped in
  `732f41c` is inert: the endpoint accepts unsigned POSTs from anyone who knows the URL, and
  a forged `pull_request closed merged` marks fixes merged, deletes branches and deletes
  Cloudflare deployments. **The highest-priority item on the user's side.**
- Alembic is scaffolded but not wired into `deploy.sh`; `schema_sync` + `create_all` remain
  the live path, and they handled today's new table without a manual step.

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
