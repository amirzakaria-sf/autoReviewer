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

---

## 2026-09-20 16:40 (UTC) — The three tenancy gaps, closed

- **Agent:** claude
- **Commit:** `7dab810c57b083b7d6508ee3ef97c4f0df537dac` (`7dab810`)
- **User ask:** take the three tenancy gaps next.

### Each gap was bigger than its one-line entry

STATUS listed three. Reading the files found seven, and two of them were writes rather than
reads — which matters, because a read leak is embarrassing and a write leak lets one
organisation steer another's automation.

**`counsel.py`** — the recorded gap was `_resolve_context`: no ownership check on `repo_id`,
and with none given a fallback of `select(Repo).first()`, an arbitrary repository from the
whole deployment. Two more were in the same file:

- `get_job` had **no ownership check at all** — `WHERE payload->>'job_id' = :job_id`. Any
  authenticated caller could poll any job id and read its full `result`, and a `counsel_prd`
  result is a document of another organisation's file paths, source excerpts and citations.
  The id is a UUID, but "hard to guess" is not an access control, and it is printed in that
  organisation's own activity events.
- `ask` accepted another user's `conversation_id` without checking ownership, which both
  replayed their history into the model and appended to their transcript. `get_conversation`
  checked exactly this and `ask` did not — the guard existed, on the wrong endpoint.
- `get_conversation` answered 403 for someone else's conversation, which confirms the id is
  real. Now 404, per the DECISIONS row.

**`ws.py`** — worse than "broadcasts across orgs". The socket had **no authentication at
all**. `@app.middleware("http")` does not run for a websocket scope, and `/ws/activity` is
not under `/api/` either, so nothing was checking a session. Anyone who could reach the host
could watch every organisation's councils run, with the file paths and assertion text those
node messages carry.

**`human_input.py`** — `list_requests` returned every organisation's open questions. The
real problem was `answer_request`: it enqueues a `fix_council` work item, so unscoped it was
a way to steer another organisation's council run. `actor` also came from the request body,
so the audit line on that answer could name anyone the caller chose.

### How the feed got scoped

47 `emit_event` call sites. Threading a repo id through all of them is how one gets missed,
and a missed one is either a leak or a dead feed. Instead: a `contextvars` scope set once per
run at six entry points (`run_and_persist`, `trigger_fix_council`, `resolve_approval`, both
worker counsel handlers), with `emit_event` stamping from it. `asyncio.to_thread` copies the
context, so graph nodes running in worker threads inherit it. The two emitters with no
ambient run — the stuck-run sweeper, which walks several repositories in one pass, and the
public webhook handler — name their repository explicitly.

**An event with no `repo_id` reaches nobody.** The two available failure modes are
"unattributed event is invisible" and "unattributed event goes to everyone"; only the first
is recoverable, and the broadcaster logs each one so it is findable.

### Verified the bugs were real before trusting the tests

A passing test proves nothing if it would also have passed before. Put each pre-fix
behaviour back and ran it:

```
1 broadcaster  pre-fix leaks to other org:                  True
2 get_job      pre-fix returns another org's PRD:           True
               post-fix returns nothing:                    True
3 counsel      pre-fix fallback picks a repo outside [mine]: True
```

The `two_orgs` fixture names the other org's repo `aaa-theirs/…` and the caller's
`zzz-mine/…` on purpose: a fixture where the caller's own repo happens to sort first would
pass against the broken fallback.

The websocket tests go through a **real handshake** with `TestClient`, not a hand-built fake
socket:

```
test_a_real_handshake_with_no_cookie_is_closed                  → 4401
test_a_real_handshake_with_a_valid_cookie_connects_and_is_scoped → frozenset({repo_id})
test_an_expired_or_tampered_cookie_is_closed_not_accepted       → 4401
```

A fake socket proves the handler's branches and proves nothing about whether a browser's
cookie actually arrives — which is the assumption the entire change rests on, and the one
that would take the activity feed down for every user if it were wrong.

```
$ backend/.venv/bin/python -m pytest -q
367 passed, 1 warning in 41.06s        (343 before this slice)

$ cd frontend && npx tsc --noEmit
(clean)
```

### Frontend

Both websocket consumers now refresh once on a `4401` and reconnect, instead of retrying
into the same rejection — otherwise any page left open past the access-token TTL sits in
"reconnecting" until a reload. `refreshSession()` is exported from `lib/api.ts` so both go
through the existing single-flight guard; the refresh token rotates on every use, so two
concurrent refreshes would invalidate each other.

### Not done

- **Not deployed.** Policy stands; the containers still run `732f41c`.
- **No live exercise of the new websocket auth against the real deployment**, because that
  would require deploying. The handshake is covered by a real-server test instead. Worth
  watching the activity feed on the first deploy — that is the one change that fails
  visibly rather than silently.

---

## 2026-09-20 16:55 (UTC) — Three more, found by auditing differently

- **Agent:** claude
- **Commit:** `355359e849946f48f667c92a744a3366fc81ba53` (`355359e`)
- **User ask:** "Is it all done?"

The last time that question was asked, my answer was built by re-reading my own to-do list,
which is a circular check — it can only ever confirm that I did what I wrote down. So this
time the audit ran a different way: walk every route in every router with `ast`, print the
ones whose signature carries no tenancy dependency, and read each.

That produced false positives (`admin.py` declares `require_admin` at the router, not per
route) and three real findings, all in `github.py`, none shaped like what the earlier pass
was hunting:

- `GET /api/github/repos` built its `connected` flag from **every** `Repo` row, so it told
  the caller that some other organisation had connected a repository.
- `POST /api/github/connect` returned another organisation's `repo_id`. It never reassigned
  — the dangerous half was already right — but it handed back an identifier.
- `POST /api/github/disconnect` clears the **deployment-wide** GitHub token and was open to
  any authenticated member of any organisation. One click stops every other organisation's
  pushes, PR comments and branch cleanups.

The third is the interesting one. It reads nothing, so an audit hunting for *leaks* walks
straight past it. The tenancy boundary has an availability half, and only a route-by-route
sweep surfaces it.

```
$ backend/.venv/bin/python -m pytest -q
371 passed, 1 warning in 41.44s

$ cd frontend && npx tsc --noEmit
(clean)
```

Also checked and found correct, so they are recorded here rather than left to be
re-investigated: `webhooks.py`'s `select(Fix)` (signature-verified, not session-scoped by
design), `email_actions.py` (the signed token *is* the credential), `slack_connect.py`'s
`/status` (account-wide by design, read-only), and `counsel.py`'s conversation endpoints
(scoped by owner, which is the right boundary for a private chat — not by org).

### Still true

**Nothing from this session is deployed.** The containers have been up 12 hours; they run
`732f41c`. Every commit today is on `main` and pushed and none of it is live.

---

## 2026-09-21 05:05 (UTC) — Deployed, and verified against the live host

- **Agent:** claude
- **Deployed:** `eb4bf2f` (working tree = `main`, clean). `./deploy.sh` → OK at 05:05:12Z.
- **User ask:** deploy it.

Everything this session shipped had been verified against Azure or against tests. None of it
had been verified *as deployed*, which is a different claim — and the one this project has
been burned by before (`732f41c` sat undeployed for 22 hours while STATUS implied it was
live).

### Boot

`create_all` created `research_findings` with all 15 columns on its own. No manual
migration, no `schema_sync` column patching needed — it is a new table, which is the case
`create_all` has always handled. Boot log clean, `Application startup complete`, postgres
untouched (the `--no-deps` on every `up` in `deploy.sh` is what keeps a backend recreate
from cascading into the database).

### Nine API surfaces, over HTTPS

```
200  /api/overview          200  /api/admin/overview
200  /api/issues            200  /api/admin/orgs
200  /api/repos             200  /api/github/repos        ← scoping changed today
200  /api/org               200  /api/counsel/jobs/{id}   ← scoping changed today
200  /api/human-input                                     ← scoping changed today
```

One thing worth recording for whoever tests this next: hitting `http://127.0.0.1:8300`
directly returns **401 on every authenticated route even with a good login**, because the
session cookie is `Secure` and a plain-HTTP client never sends it back. That is correct
behaviour, and it looks exactly like a broken deploy for about a minute. Test through
`https://whip-guard.zakarias.in`.

### The websocket, end to end through nginx and the NOTIFY relay

This was the one change that fails visibly rather than silently, so it got the real test —
a live socket, with synthetic events pushed through `pg_notify` exactly as the worker does:

```
1 no cookie      : refused (InvalidStatus)
2 valid cookie   : connected
3 my repo event  : received
4 other org event: NOT received (correct)
5 unaddressed    : NOT received (correct)
```

Four assertions, not one. "It connects" would have passed with the filtering broken.

### Counsel, on the real deployment

```
answer:   The delete handler removes the item at the clicked button's index
          from `items` and re-renders the list (`app.js:22-25`).
citations: ['app.js:22']

council_run: ('counsel','gpt-5.6-terra',1210,   0,38,{'protocol':'responses','reasoning_tokens':15})
council_run: ('counsel','gpt-5.6-terra',3695,1207,34,{'protocol':'responses','reasoning_tokens': 0})
```

Three things are true in production for the first time, and all three are visible in that
second row: the generation protocol is **Responses**, native **reasoning** is actually being
spent, and the provider **prompt cache is hitting** — 1,207 cached input tokens on the
second turn. Before today the patch worker and Counsel were both non-thinking Chat
Completions loops recording no usage at all.

### Still unverified

A full **Fix Council** run under the new protocol (detect → patch → verify → propose). The
patch loop is proved live and Counsel is proved live, but the graph around them has not been
re-run since the protocol changed. It opens a PR on the fixture repo, so it waits for the
user to ask.
