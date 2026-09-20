# Changelog

Short product history. Append a dated heading when a slice lands. Detail belongs in
`docs/<agent>/changes.md`; this is the shape of the product over time.

## 2026-09-20 — the tenancy boundary closed

The scoping pass that taught `api.py` and `fix_review.py` to read `repos.org_id` had stopped
there. Counsel resolved its repository with no ownership check and fell back to an arbitrary
one from the whole deployment; its job endpoint returned any PRD's full result to any
authenticated caller; `ask` accepted another user's conversation id. The activity websocket
had no authentication at all and wrote every event to every connection. The clarification
endpoints listed every organisation's questions and let anyone answer them — which enqueues
a `fix_council` work item, so that one was a way to steer another organisation's council,
not merely to read it.

All of it is scoped now, 404 rather than 403 across the boundary. Activity events carry a
`repo_id` stamped from an ambient per-run scope, and an unaddressed event reaches nobody.
The websocket authenticates its own handshake, and both browser consumers refresh once on a
4401 rather than reconnecting into the same rejection.

## 2026-09-20 — Azure Responses, apply_patch, and curated web research

Every generation call moved to the Responses API, so the Fix Council's patch loop can carry
native reasoning for the first time — on GPT-5.x a tool-bound Chat Completions call cannot,
which meant an eight-tick loop that had never thought. `fix_council.py` and
`counsel/agent.py` stopped building their own clients; usage is recorded for every turn with
the protocol and reasoning cost. Jury and Arbiter output became a forced Pydantic function
instead of JSON asked for in prose, and `prompt_cache_key` is finally sent.

`apply_patch` replaced `write_file` as the default edit: an exact anchor replaced in place,
so a patch can only change the bytes it names. Full rewrites remain available for creation.

New: a web-research council. A Gatherer runs the model's own search; a Curator attributes
each claim to a source that was actually returned, scores it, and drops the rest. Kept and
rejected findings are both persisted. The PRD council plans research per requirement and
cites it; Counsel and the patch worker can call it themselves.

## 2026-09-20 — two-agent documentation flow

`docs/info.md`, `STATUS.md`, `DECISIONS.md`, `ARCHITECTURE.md`, agent folders, `CLAUDE.md`
and `AGENTS.md`. Same protocol as `jobFlowAuto`, `aiClass` and `opencode`.

## 2026-09-19 — hardening

GitHub webhook signature verification and durable delivery dedupe. A real HTTP probe of the
Cloudflare preview, replacing a hardcoded `reachable: True`. Secret values redacted out of
findings. A global kill switch. Per-repo Pages project and per-org GitHub App installation.
`ask_human` now blocks the Fix Council instead of only recording the question. Alembic
scaffolding and a boot refusal on demo credentials.

## 2026-09-18 — organizations, end to end

Platform admins create organizations; org admins invite their team by email; `/join`
accepts an invitation straight into a live session; repositories inherit the connector's
org. Designations, routing rules and git identity links became editable — identity links
suggest the addresses actually in each repository's history. Tenancy scoping added to the
product read and action surfaces.

## 2026-09-17 — the review flow

Approve, reject, and **ask for changes**. Nothing reaches GitHub until approval: the diff is
stored and reviewed in the dashboard, and a rejection leaves the repository untouched. Each
category is now verified by its own detector, before and after deploy. The workspace moved
to the data disk, laid out per organization.

## 2026-09-16 — Counsel, orgs, performance

The Counsel chat agent (explain a subsystem, attribute code, draft a PRD, dispatch the other
councils). Organization model with role / designation / seniority as three independent axes.
Pooled synchronous connections.

## 2026-09-15 — memory, retrieval, the security split

Hybrid retrieval (structural + BM25 + dense + graph, fused by RRF), memory traces,
token-budgeted prompt compilation. The privileged worker split out from the web process.
Refresh-token grace window.
