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

---

## 2026-09-20 11:05 (UTC) — Azure Responses, apply_patch, and curated web research

- **Agent:** claude
- **Commits:** `6093ea89a0c488db480d77a78357c0760267678f` (`6093ea8`) — protocol + edit
  primitive. `fbcc5b6539342cb3a7aebec5f7e0e3b0b596e059` (`fbcc5b6`) — the research council.
- **Plan:** the user wrote one and handed it over;
  [`../plans/2026-09-20-azure-responses-and-apply-patch.md`](../plans/2026-09-20-azure-responses-and-apply-patch.md)
  records it along with the three places live probing contradicted it.
- **User ask:** these Azure models are raw — no system prompts, no memory, context
  management is ours. Move generation onto the Responses API the way `opencode` does, prefer
  an exact-anchor edit over full-file rewrites, and take the PRD to the next level with a
  real research flow: web research happens, the agent checks it, and it decides what to keep.

### Probed live BEFORE writing anything

The plan's central premise was that GPT-5.x cannot combine tools with reasoning on Chat
Completions. That is worth believing, but it is not worth building on unverified, and two of
the plan's own instructions turned out to be wrong.

```
probe 1  responses + reasoning, no tools          200, status=completed
probe 3  effort=high, real prompt                 types=['reasoning','message'] reasoning_tokens=463
probe 2  responses + reasoning + flat tool        200, function_call returned
probe 8  tools=[{"type":"web_search"}]            types=['reasoning','web_search_call','message']
                                                  annotation: url_citation → expressjs.com/en/guide/migrating-5/
```

A trivial prompt spends **0** reasoning tokens even at `effort=medium`, which briefly looked
like "reasoning is inert on these deployments". It is not — a prompt with something to think
about spends hundreds. Worth knowing before anyone reads a future log and concludes the
feature is broken.

**Deviation 1 — the SDK pin.** The plan said `openai>=1.58.1,<2.0.0`. Installed here is
**3.13.0**:

```
$ .venv/bin/python -c "import openai; ...signature(...responses.create)..."
openai 3.13.0
prompt_cache_key present: True
```

The cap would have been a two-major downgrade for no gain. Shipped without an upper bound.

**Deviation 2 — the web-research transport.** The plan pointed at `opencode`'s Azure AI
Foundry agent. It is enabled, and it is broken:

```
GET  {project}/agents?api-version=v1     200  → web-research:1, model "gpt-5.2-chat",
                                                tools: [{"type":"web_search"}]
POST {project}/openai/v1/responses       404  DeploymentNotFound   (×4 invocation shapes)
```

Its entire definition is one `web_search` tool — the same tool that works directly on our own
deployments. Asked the user, who chose native. No second endpoint, no second key, nothing to
repair in a portal.

### Shipped

- **`app/azure_client.py`** — `complete_turn` is now the only generation entry point.
  Responses first; `_to_chat_messages` is the only place the Chat shape exists. Reasoning
  items replayed with `id`/`summary` only; tools sent flat with `strict: false` and `$defs`
  intact; jury and Arbiter output is a forced Pydantic function, with the *semantics* (weights
  sum to score, when to ask for clarification) left in the prompt where they belong. A 400
  drops the one parameter its body names and retries once — anything else raises with the
  body logged, because a blanket fallback is how a sibling app swallowed content-filter
  rejections for weeks while looking healthy.
- **`app/sandbox/apply_patch.py`** — exact-anchor replace, the preferred edit. `write_file`
  kept for creation and genuine full replaces. Both check `scope_excludes` *inside the
  primitive*, so a future call site cannot forget it.
- **`app/research.py`** — Gatherer + Curator. The mechanical half of curation is the part
  that matters: a claim whose URL was not among the sources the search actually returned is
  dropped regardless of what the Curator said. Both keeps and rejections persist to
  `research_findings`.
- **Wiring** — PRD council (per-requirement planner, `research_web` findings cited in a
  "What the web says" section), Counsel (`research_web` read tool), patch worker
  (`research_web`, its own bounded sub-call).
- **Frontend** — the Counsel markdown renderer gained bare-URL links and `_italics_`. A
  citation the reader cannot click is barely a citation, and that renderer only handled
  backticks and bold.

### Verified

```
$ backend/.venv/bin/python -m pytest -q
343 passed in 39.44s          (321 after the protocol slice; 270 at session start)

$ cd frontend && npx tsc --noEmit
(clean)
```

**Live patch-loop round trip** — the shape that is a 400 in production and invisible against
a mock:

```
tick 0: protocol=responses reasoning_items=1 reasoning_tokens=8  calls=['read_file']
tick 1: protocol=responses reasoning_items=1 reasoning_tokens=12 calls=['apply_patch']
tick 2: protocol=responses reasoning_items=0 reasoning_tokens=0  calls=['finish_patch']

history item kinds: ['system','user','reasoning','function_call','function_call_output',
                     'reasoning','function_call','function_call_output',
                     'function_call','function_call_output']

council_run: ('live_probe_patch_worker','gpt-5.6-terra',603,0,29,
              {'protocol':'responses','reasoning_tokens':8})
probe rows removed: 3
```

The model reached for `apply_patch` rather than `write_file` without being told to, which is
what the tool descriptions were written to achieve.

**Live research run** — real question, real search, real curation:

```
queries: ['site:expressjs.com Express 5 app.del removed app.delete migration guide',
          'site:github.com/expressjs/express app.del removed Express 5']
sources: ['https://expressjs.com/en/guide/migrating-5/']
KEPT   [official/current rel=100] Express 5 removes the `app.del()` alias; `app.delete()` is
                                  the replacement...
       (3 more, all attributed to that one source)
persisted rows: 4 kept → then deleted
```

Both live probes ran against `whipguard_test`, **never** production — `DATABASE_URL` was
rewritten in the probe script before `app.config` was imported, and every row written was
removed afterwards. The one time this project ran tests against the production database, the
fixtures appeared on the dashboard as real issues.

### Not done, deliberately

- **Not deployed.** `deploy.sh` builds from the working tree; the user asks.
- **The three tenancy gaps stay open** and stay in STATUS. Folding them into this diff would
  have made an un-reviewable PR. `counsel.py` is now slightly more urgent than it was:
  Counsel holds `research_web`, so an unscoped `_resolve_context` also means one org's
  question can spend another org's research budget.
- **No fixture Fix Council run.** The patch loop was proved live; the graph around it
  (detect → patch → verify → propose) has not been re-run end to end under the new protocol.
  Listed under Needs verification.
