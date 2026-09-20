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
- **Commits:** `6093ea89a0c488db480d77a78357c0760267678f` (`6093ea8`) — Azure Responses +
  `apply_patch`. `fbcc5b6539342cb3a7aebec5f7e0e3b0b596e059` (`fbcc5b6`) — the curated
  web-research council.
- **Deployed:** **no.** Both commits are on `main` and pushed; the running containers are
  still the 2026-09-20 build of `732f41c`. `deploy.sh` builds from the working tree, so
  nothing above is live until someone asks for a deploy. The new `research_findings` table
  is created by `create_all` at boot, so the first deploy applies it with no manual step.

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
  - Docs: `docs/plans/2026-09-20-azure-responses-and-apply-patch.md` (with the places live
    probing contradicted the plan marked), 8 new `DECISIONS` rows, README `Model protocol`
    and `Web research` sections, `ARCHITECTURE.md`, `CHANGELOG.md`.

- **Verified this session:**
  - `pytest -q` → **343 passed** (was 321 after the protocol slice, 270 before the session).
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

- **Nothing shipped this session is unexercised** — the protocol, the patch loop and the
  research council were each run against live Azure, and the results are in
  [`claude/changes.md`](./claude/changes.md).
- **Not yet seen end to end:** a full Fix Council run on the fixture repo under the new
  protocol (detect → patch → verify → propose). The loop was proved live, the graph around
  it was not re-run. Worth one fixture run on the next deploy.
- `GITHUB_WEBHOOK_SECRET` is **still unset on both sides**, so the signature check added in
  `732f41c` remains inert — the endpoint still accepts unsigned POSTs.
- Alembic is scaffolded but not wired into `deploy.sh`; `schema_sync` remains the live path.

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
  Note `counsel.py` is now more urgent than it was: Counsel holds `research_web`, so an
  unscoped `_resolve_context` also means one org's question can spend another org's budget.
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
