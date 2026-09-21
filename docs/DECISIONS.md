# Decisions

Lasting choices. **Add a row when you make one.** Do not reverse a row without the user
asking.

Format: `YYYY-MM-DD` · **title** · decision · why · who.

---

## Architecture

- **2026-09-15** · **The security split** · The web process has no `/var/run/docker.sock`
  and a read-only workspace. Everything privileged is queued into `work_items` and executed
  by `app/worker.py`, which has the socket and no inbound port. · A process that renders
  model output and untrusted repository text, while holding the Docker socket and deploy
  credentials, is one RCE away from the host. · claude (plan.md §15)

- **2026-09-16** · **The worker has no `build:`** · It runs
  `image: whipguard-backend:latest`, the tag the backend service builds. · With its own
  build stanza Compose produced a separate image, and `docker compose build backend` left
  the worker on old code. Three deploys ran stale code with no visible error — containers
  healthy, queue draining, the new code simply absent. · claude

- **2026-09-18** · **Tenancy has exactly one source of truth** · `repos.org_id` is the only
  column carrying it; issues, fixes, traces, chunks and symbols reach their org *through*
  their repo. Reads scope through `app/deps.py`'s `visible_repo_ids`. · A denormalised
  `org_id` on twelve tables drifts, and then no query is trustworthy. · claude

- **2026-09-18** · **Cross-org reads are 404, never 403** · A repo, issue or fix outside
  the caller's org reports as missing. · A 403 confirms the id names something real, which
  turns the tenancy boundary into a lookup service for the other side of it. · claude

- **2026-09-17** · **The workspace lives on the data disk, at the same absolute path on
  both sides** · `WORKSPACE_HOST_ROOT` on `/dev/sda`, bind-mounted at `/srv/workspace` for
  the host *and* the containers; layout `<org-slug>/<owner>__<repo>/`. · The root disk had
  6.6G free and a full root disk takes down every service on the box. The identical path is
  not tidiness: a git worktree records its link to the mirror as an absolute path, so under
  any other host name every worktree resolves only inside the container. · claude

- **2026-09-14** · **One Azure call site** · Every generation call goes through
  `app/azure_client.py`. · Token and cost accounting has to happen exactly once. · claude
  · *Reaffirmed 2026-09-20: `fix_council.py` and `counsel/agent.py` had each been building
  their own `AzureOpenAI` and recording nothing, which made this row untrue in practice for
  the two most expensive call sites in the product. Both now call `complete_turn`.
  `app/embeddings.py` stays separate — a different API surface, nothing to unify.*


- **2026-09-20** · **Responses is the generation protocol** · Every generation call uses the
  Responses API at `{endpoint}/openai/v1/` with `api_version="preview"`. Chat Completions
  survives as the `WHIPGUARD_AZURE_API=chat` operator hatch and the documented fallback,
  never the default. · On GPT-5.x a tool-bound Chat Completions call cannot carry a
  non-`none` reasoning effort, so the Fix Council's eight-tick patch loop — the one call in
  this product that most needs to think — had reasoning switched off for its entire life.
  Verified live on `gpt-5.6-terra` and `gpt-5.6-luna` before the rewrite, not inferred from
  documentation. · claude

- **2026-09-20** · **A 400 is not evidence the protocol is unsupported** · On
  `BadRequestError`, drop the one optional parameter the body actually names
  (`prompt_cache_key`, then `reasoning`) and retry once. Anything else raises with the body
  logged. · A blanket "fall back to Chat Completions on any 400" swallowed content-filter
  rejections and malformed tool schemas identically in a sibling app: the product looked
  healthy and every call had quietly lost its reasoning. Losing a cache partition is
  cheaper than losing the protocol, and both are cheaper than losing the error. · claude

- **2026-09-20** · **`strict: false` on every converted tool** · Tool schemas are sent flat
  with `strict` explicitly false. · The Responses API defaults it to true server-side,
  which requires `additionalProperties: false` and every property in `required` — neither
  of which our hand-written schemas guarantee. Unset, every tool-bound call 400s, not just
  the nested ones. `$defs` is carried through for the same class of reason: Pydantic emits
  `$ref` for nested models, and a dangling ref is rejected outright. · claude

- **2026-09-20** · **The SDK is not capped below 2.0** · `openai>=1.58.1`, no upper bound.
  · 1.58.1 is the floor where `prompt_cache_key` is a real kwarg; below it the call
  TypeErrors client-side. The sibling project's `<2.0.0` pin would have been a two-major
  downgrade from the 3.13.0 installed and verified here. · claude

## Product rules

- **2026-09-13** · **WhipGuard never merges a PR** · No merge capability exists and none
  may be added. A human merging on GitHub is a real resolution, synced back by the
  `pull_request` webhook. · The forbidden-action rule: the system proposes, a human
  disposes. · original (plan.md §1)

- **2026-09-17** · **Nothing reaches GitHub until a human approves** · A proposal is local
  — the diff is stored on `Fix.diff` and reviewed in the dashboard. `resolve_approval`
  pushes the branch and opens the PR. · Previously a rejected fix left an open draft PR and
  a pushed branch behind forever, because nothing in the codebase ever closed either. · user

- **2026-09-17** · **Three verbs, not two** · Approve, reject, and **ask for changes**. The
  approve endpoint refuses `approved=false` and redirects to the revision endpoint. · "No"
  and "no, do it this way instead" are different outcomes, and a boolean throws away the
  only judgement a human brings that the council cannot. Modelled on the sibling `opencode`
  project's plan-approval flow. · user + claude

- **2026-09-19** · **Secrets never appear in a finding's evidence** · The secret scanner
  reports `path:line: label`, never the matched value. · Evidence is copied into a GitHub
  issue body, the dashboard and Slack. A leaked-credential detector that republishes the
  credential is worse than none. · cursor

- **2026-09-16** · **Never fall back to `settings.fixture_repo`** · If a repo cannot be
  resolved, log and refuse. · Eight separate bugs: a Fix Council that cloned the fixture for
  every issue, a poller that polled only the fixture, a PR opened against the wrong
  repository. · claude + cursor

- **2026-09-20** · **`apply_patch` is the default edit** · An exact-anchor replace is the
  preferred write; `write_file` is for creating a file or a genuine full replace. Both are
  gated by the same `scope_excludes`, inside the primitive rather than at the call site. ·
  A full-file rewrite is how a green detector still ships a collateral regression: the
  detector only checks the behaviour it was written to check, so a model that restates
  2,000 lines to change three can drop or reword a sibling function and still pass. An
  anchored replace can only change the bytes it names. Exact match, never fuzzy — a patcher
  that "mostly" finds its anchor eventually applies an edit somewhere subtly wrong, and
  nothing downstream can see that it happened. · claude

- **2026-09-20** · **Web research is curated before it is used** · A Gatherer runs the
  model's own `web_search`; a Curator then attributes every claim to a URL the search
  actually returned, scores relevance/recency/authority, and drops the rest. A claim whose
  URL was not among the returned sources is dropped mechanically, whatever the Curator
  said. Both keeps and rejections are persisted in `research_findings`. · A single model
  asked to search and summarise repeats a four-year-old blog post in the same confident
  voice it uses for the official changelog, and will supply a plausible URL from memory when
  it has none. Attribution to a source that was really returned is the only mechanical
  check available. Persisting the rejections matters because "we looked and chose not to
  use it" is a different state from "we never looked". · claude

- **2026-09-20** · **The native `web_search` tool, not a Foundry agent** · Research calls
  `{"type": "web_search"}` on our own deployments rather than the Azure AI Foundry
  `web-research` agent the sibling `opencode` project uses. · That agent's entire definition
  is one `web_search` tool, so the extra project endpoint, key and hop buy nothing — and as
  of 2026-09-20 it is broken anyway: its definition names a `gpt-5.2-chat` deployment that
  no longer exists, so every call returns 404 `DeploymentNotFound`. Checked across four
  invocation shapes before deciding. · claude (user chose this option)

- **2026-09-20** · **No keyword list decides when to research** · Every research entry
  point is the model's own judgment: a per-requirement planner in the PRD council, and an
  autonomous `research_web` tool in Counsel and the patch worker. · A static list of
  technology names fails on the service someone integrates next week, and maintaining one
  is a permanent tax. A tool with a clear description and a per-run ceiling costs nothing
  when it is not needed. · claude (protocol taken from `opencode`'s own notes)

## Organizations and access

- **2026-09-18** · **A platform admin creates organizations; org admins invite their own
  team** · `/admin/orgs` creates and names a first admin; `/api/org/invites` is org-admin
  only. · Matches the existing access-request gate — nothing exists until an admin approves
  it — without putting the platform admin in the loop for every hire. · user

- **2026-09-18** · **One organization per person, for now** · `add_member` refuses a second
  membership with a named error. · Every org-scoped read resolves the caller's org as
  `orgs_for_user(...)[0]`. A second membership did not fail — it silently decided which org
  someone saw by which was created first. Refusing is the honest version of the same
  constraint. `org_members` already supports many, so widening later is not a schema change.
  · user

- **2026-09-18** · **An org arrives usable** · `create_org` seeds six designations and six
  routing rules. · An org with neither can route nothing, and "configure six things before
  anything works" is not a discoverable onboarding step. · claude

- **2026-09-16** · **Slack is connected once per organization, not per repo** · One channel
  receives every repo's approvals; installs are keyed by `team_id`. · A per-repo channel
  made the user repeat an OAuth round-trip for every repository and bought nothing.
  `team_id` is the only thing identifying whose workspace an inbound button click came from.
  · user


- **2026-09-20** · **The activity feed is addressed, not broadcast** · Every event carries a
  `repo_id`, and a connection receives only events for repositories its viewer can already
  see. An event with **no** `repo_id` reaches nobody. · The socket was the widest hole in
  the tenancy boundary: no authentication at all (`@app.middleware("http")` does not run for
  a websocket scope, and `/ws/activity` is not under `/api/` either) and `broadcast` wrote
  every event to every open connection, node messages and their file paths included. Of the
  two available failure modes — an unattributed event is invisible, or an unattributed event
  goes to everyone — only the first is recoverable. · claude

- **2026-09-20** · **One ambient scope, not 47 threaded arguments** · `activity_scope` /
  `set_event_repo` is set once per run at each entry point (`run_and_persist`,
  `trigger_fix_council`, `resolve_approval`, the worker's job handlers), and `emit_event`
  stamps from it. · There are ~47 `emit_event` call sites. Threading a repo id through all
  of them is how one gets missed, and a missed one is either a leak or a dead feed. Six set
  points is a number a reviewer can check. `asyncio.to_thread` copies the context, so a
  graph node running in a worker thread still sees it. · claude

- **2026-09-20** · **Answering a clarification is a write, and is scoped like one** ·
  `POST /api/human-input/{id}/answer` checks tenancy before doing anything, and the actor is
  taken from the session rather than the request body. · The endpoint enqueues a
  `fix_council` work item, so unscoped it was not a read leak but a way for one organisation
  to steer another's council run — and `actor` from the body meant the resulting audit line
  could name anyone the caller chose. · claude


- **2026-09-20** · **Deployment-wide credentials are platform-admin only** ·
  `POST /api/github/disconnect` clears the single shared GitHub token, and is now gated on
  `require_admin`. · Any member of any organisation could otherwise stop every other
  organisation's pushes, PR comments and branch cleanups with one click. It reads nothing,
  which is exactly why an audit looking for leaks walked past it — the tenancy boundary has
  an availability half too. · claude

- **2026-09-20** · **A repository is never reassigned between organisations** ·
  Connecting one another organisation already owns is a 409; only a repository with no
  `org_id` at all (pre-tenancy) is adopted. · Reassigning would hand the new organisation
  the old one's issues, fixes, traces and findings, since all of them reach their org
  through `repos.org_id`. The 409 does disclose that the repository is connected somewhere
  in the deployment — unavoidable, because `github_full_name` is UNIQUE and the alternative
  is an integrity error the caller cannot act on, and the caller already has GitHub access
  to that repository. · claude


- **2026-09-21** · **The public VAPID key is served from the API, not only inlined at build
  time** · `/api/push/status` returns it; the client reads it from there. · Next inlines
  `NEXT_PUBLIC_*` during `next build`, so a key added to `.env` afterwards is simply absent
  from the running bundle — and the symptom is "push silently does nothing" with a checkout
  that looks correct. A sibling project spent a session on exactly that and initially
  reached the wrong conclusion by grepping a stale `.next/`. Reading it at runtime deletes
  the whole class of problem. · claude

- **2026-09-21** · **A push subscription is re-bound on every authenticated session** ·
  `syncPushSubscription()` runs from the auth gate, not from the settings page, and the
  server upserts on `endpoint`. · A subscription belongs to the ORIGIN and the service
  worker, not to a login session: it survives logout and account switches. Bound once at
  subscribe time, a device that subscribed as one person delivers to that person forever
  while the settings toggle reads "enabled", because all it can see is browser state. It is
  in the auth gate because the settings page is rarely opened, which is precisely why this
  goes unnoticed. · claude

- **2026-09-21** · **Logout drops the server row, never the browser subscription** ·
  `releasePushBinding()` deletes the row; it does not call `subscription.unsubscribe()`. ·
  Unsubscribing destroys the browser-level subscription and forces a fresh permission grant,
  which iOS will not reliably re-prompt for once dismissed. Keeping it lets the next login
  re-bind instantly. · claude

- **2026-09-21** · **Push is addressed through `repos.org_id`** · `send_for_repo` resolves a
  repository to its organization and pushes to every active member. · A finding belongs to a
  repository, not to whoever happened to trigger the scan. Routing to the triggering user
  would mean the only person who ever hears about a defect is the one already watching. Same
  column that carries tenancy everywhere else. · claude

- **2026-09-21** · **An unaddressed activity event and an unnotified push are both fail-closed**
  · Push sits inside the existing `should_notify` guard at all three notification sites. · A
  flapping condition that is not worth an email is not worth a phone buzzing. Sharing the
  dedupe means the three channels cannot disagree about what counts as an event. · claude

## Reliability and naming

- **2026-09-15** · **Refresh-token reuse has a 60-second grace window** · A rotated token
  presented again inside the window returns a fresh access token without re-rotating,
  instead of revoking the family. · Rotation-on-every-use treats a concurrent refresh burst
  — which a normal page load produces — as token theft, and logged every user out on every
  redeploy. · claude

- **2026-09-17** · **Each category is verified by its own detector** · Both the pre-push
  re-verify and the post-deploy oracle call `get_detector(issue.category)`; the interface
  takes `base_url` so browser-driven categories hit the live preview and static scanners
  ignore it. · Both gates ran the repo's Playwright suite for every category, so a
  documentation fix was judged by UI tests — and on a repo with any other unfixed bug that
  suite fails, so the approval aborted before pushing. · claude

- **2026-09-17** · **An unconfigured surface is not a disagreement** · The cross-app
  outcome checker fails closed on disagreement, but Slack being disconnected reads as
  absent. · Otherwise every otherwise-perfect deploy failed on the absence of an integration
  nobody had set up. · claude

- **2026-09-18** · **It is "confidence", not "score"** · Assurance confidence and
  resolution confidence in all user-facing copy. Database columns stay
  `assurance_score` / `resolution_score`. · These are rubric-weighted judgements an Arbiter
  assigns against a threshold, not a count of anything, and "score" alone did not say which
  of the two numbers on the page you were reading. The wire format and the vocabulary are
  allowed to differ; renaming columns would be a migration for zero user-visible gain. · user

- **2026-09-18** · **`schema_sync` is the live schema path; Alembic is for destructive
  changes only** · `create_all` plus `app/schema_sync.py` add missing tables, columns and
  enum labels at boot. Alembic is scaffolded but not wired into `deploy.sh`. · `create_all`
  silently never applies a column added to an existing table, or a new label on an existing
  enum — several columns and a whole table existed only because someone had run DDL by hand,
  so a fresh database came up broken. Additive reconciliation is safe to automate; a drop or
  a rename is not, and must not be taught to guess. · claude

- **2026-09-18** · **Tests run against their own database** · `tests/conftest.py` rewrites
  `DATABASE_URL` to `whipguard_test` before `app.config` is imported, and drops/recreates it
  each session. · Tests used to run against production: fixture rows showed on the dashboard
  as real issues, and a run that failed part-way left them there. Second incident of
  production state leaking into tests — the first was the LLM response cache. · claude

- **2026-09-20** · **Two-agent documentation flow adopted** · `docs/info.md` is the
  protocol; `docs/STATUS.md` is the handoff; each agent keeps `docs/<agent>/changes.md`.
  `CLAUDE.md` and `AGENTS.md` are pointers, not copies. · Cursor and Claude both work this
  repo and share a filesystem but not a chat. The flow is the one already running in
  `jobFlowAuto`, `aiClass` and `opencode`; keeping it identical means a lesson learned in one
  repo is legible in all four. · user
