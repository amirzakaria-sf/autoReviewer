# Architecture map

Short. The full description of what ships is [`README.md`](../README.md); the design essay
is [`plan.md`](../plan.md). Where any of them disagree with the code, **the code wins** and
you update the file in the same change.

## Two processes, one image

| Process | Has | Does |
|---|---|---|
| `backend` (`app.main`) | no Docker socket, read-only workspace, port `8300` | HTTP, WebSocket relay, all reads and decisions |
| `worker` (`app.worker`) | Docker socket, writable workspace, **no inbound port** | everything privileged, claimed from `work_items` |

They share `whipguard-backend:latest` — the worker has no `build:` (see DECISIONS). The
arrow between them points one way, through Postgres: the web side calls
`work_queue.enqueue`, the worker claims with `FOR UPDATE SKIP LOCKED`, and progress returns
over `pg_notify` → `routers/ws.py` → the browser.

## Request path

`routers/` is the only HTTP surface. `@app.middleware("http")` enforces the session for
everything under `/api/` — **it does not run for websockets**, so `ws.py` authenticates its
own handshake from the `access_token` cookie.

| Router | Owns | Org-scoped? |
|---|---|---|
| `api.py` | overview, repos, issues, fixes, approve/reject, scan, trigger-fix | yes (`deps.visible_repo_ids`) |
| `fix_review.py` | the review thread, "ask for changes" | yes |
| `org.py` | members, invites, designations, routing, identity links | yes (by membership) |
| `admin.py` | users, access requests, organizations, usage, redeploy, kill switch | platform admin |
| `auth.py` | login, refresh, access requests, `/join` | public |
| `github.py`, `slack_connect.py` | OAuth round-trips, repo connect | yes |
| `webhooks.py` | GitHub + Slack inbound | signature-verified, not session-scoped |
| `counsel.py` | chat agent, conversations, job polling | yes (repo, and conversations by owner) |
| `human_input.py` | clarification questions and answers | yes (through issue → repo) |
| `ws.py` | activity stream | yes — authenticates the handshake itself, then filters per connection |
| `email_actions.py` | one-click approve/reject from email | signed token *is* the credential |

## The councils (`app/graphs/`)

- `bug_council.py` — detect → Skeptic ‖ Corroborator → mechanical recheck → Arbiter.
  Produces an Issue with an **assurance confidence** and a rubric.
- `fix_council.py` — retrieval → patch generation (ReAct loop, tools incl. `ask_human`) →
  verifier → Arbiter. Produces a Fix with a **resolution confidence** and a diff. Writes
  nothing to GitHub.
- `approval_graph.py` — `resolve_approval` is the single seam every surface calls
  (dashboard, Slack, email, GitHub comment). On approve it pushes the branch, opens the PR,
  deploys to Cloudflare Pages, re-runs the category detector against the **live** URL, then
  runs the cross-app outcome check.
- `outcome_checker.py` — reads GitHub, Cloudflare, Slack and the dashboard back
  independently and fails the run closed on disagreement.

Categories are a registry (`app/categories.py`) plus a detector module
(`app/detectors/<key>.py`). A new category is a config row and a detector, never new
pipeline code. Every detector takes `(worktree_path, path_scope, base_url)`.

## Model calls

`app/azure_client.py::complete_turn` is the only generation client — jury, Arbiter, patch
loop, Counsel, PRD council. It speaks the **Responses API** (`{endpoint}/openai/v1/`,
`api_version="preview"`) so a tool-bound call can carry native reasoning, which Chat
Completions cannot do on GPT-5.x. Callers pass Responses input items; the Chat shape exists
only behind the `WHIPGUARD_AZURE_API=chat` hatch. `app/embeddings.py` is a separate client
on a separate API and stays that way.

`app/research.py` is the web-research council: a Gatherer running the built-in `web_search`
tool, then a Curator that attributes each claim to a source actually returned and drops
what it cannot. Both halves land in `research_findings`. Bound into the PRD council (via a
per-requirement planner), Counsel and the patch worker as `research_web`.

## Data

One Postgres, pgvector. Schema is built at boot by `create_all` + `app/schema_sync.py`
(additive columns and enum labels). `sync_db.py` is the pooled synchronous connection used
by everything running off the event loop — retrieval, the call graph, memory traces, org
membership.

Tenancy: **`repos.org_id` is the only column that carries it.** Everything else reaches its
org through its repo.

Retrieval is hybrid — `hybrid_retrieval.py` fuses structural, lexical (`lexical_index.py`,
BM25), dense (`retrieval.py`, pgvector) and graph (`graph_index.py`) channels with
reciprocal rank fusion, and `context_broker.py` compiles the result under one token budget
(`prompt_compiler.py`).

## Workspace on disk

```
$WORKSPACE_HOST_ROOT/<org-slug>/<owner>__<repo>/
    mirror/              bare --mirror clone, fetched on webhook/poll
    counsel/             persistent checkout of the default branch, for reads
    fixes/<n>-<slug>/    one worktree per active fix
```

Bind-mounted at `/srv/workspace` on the host **and** in the containers — the same absolute
path, because worktrees record their mirror link absolutely. See `info.md` §11.

## Frontend

Next.js App Router, `frontend/app/`. `lib/api.ts` is the only fetch client (it owns 401
refresh, `OfflineError`, and the 409-means-refresh convention). `components/AuthGate.tsx`
gates everything except `/`, `/login`, `/signup`, `/accept-invite`, `/join`.

Ports: postgres `5433`, backend `8300`, frontend `3300` — all on `127.0.0.1`, TLS at the
host nginx.
