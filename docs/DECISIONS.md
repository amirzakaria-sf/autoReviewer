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

- **2026-09-14** · **One Azure call site** · Every model call goes through
  `app/azure_client.py`. · Token and cost accounting has to happen exactly once. · claude

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
