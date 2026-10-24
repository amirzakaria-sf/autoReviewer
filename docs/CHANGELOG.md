# Changelog

Short product history. Append a dated heading when a slice lands. Detail belongs in
`docs/<agent>/changes.md`; this is the shape of the product over time.

## 2026-10-24 — `CONSOLE_MANAGED`: request review and Redeploy hidden when the operator console owns them

`CONSOLE_MANAGED=true` (default off) adds `console_managed` / `console_url` to `GET /api/admin/overview`; the admin dashboard then
hides the "Pending requests" card and the in-app **Redeploy** card, and the Users page hides the request review sections, each with a
pointer to the console. Organizations and user role / status management stay (the console cannot change roles). UI only: the
endpoints (including `/api/admin/redeploy`) are unchanged and unsetting the variable restores everything. Test:
`test_admin_overview_tells_the_dashboard_whether_the_console_owns_requests`.

## 2026-10-24 — Operator-console contract, onboarding at parity with Whip and Lumen, escaped HTML mail

- **`/api/console/v1/*`** (`routers/console_api.py`): the contract the shared operator console uses. `X-Console-Token` must equal
  `CONSOLE_API_TOKEN` (>= 32 chars, constant time); unset, short or wrong is a 404 for everyone. Summary, usage (per role and model:
  `council_runs` has no user), users, suspend / restore (administrators protected, refresh tokens revoked), access requests with
  approve / reject / revoke. Quota answers 409 — WhipGuard has no per-user allowance and says so. Approving puts the person in the
  deployment's only organization (none or several: no guess). Approve and reject now live in `access_decisions.py`, shared by the
  in-app admin endpoints and the console, so the same state change and the same mail happen from either.
- **Email code on the accept-invite page** (`access_otp.py`, `/api/auth/invite/send-code` and `/verify-code`, a step in
  `frontend/app/accept-invite`): request → approval → emailed invite link → 6-digit code → password, the same sequence as Whip and
  Lumen. 10-minute code, 5 attempts, 60-second resend gap, HMAC stored (never the code), verified state good for 30 minutes.
  Skipped when `INVITE_REQUIRE_OTP=false`, when SMTP is off, and for the very first account. Five nullable columns on
  `access_requests`, added by `schema_sync` at boot. Old invite links keep working — they just ask for the code first.
- **Mails**: one HTML shell (`integrations/email_templates.py`) with a plain-text part for every mail: request received, new request
  (operator), invite, declined, welcome, verification code, plus the existing fix-proposed / status / org-invite mails moved onto
  it. **Everything user-supplied is now escaped** — a name, reason or issue title used to be pasted into the HTML. `email_client`
  keeps its `build_*` names. The new-request mail was being `await`ed on a synchronous function (mail went out, then a TypeError was
  logged); it now runs in a thread. Request-access is limited to 5 per hour per client IP.
- Tests: `tests/test_access_otp.py` (10) and `tests/test_console_api.py` (8). `conftest.py` now blanks SMTP for every test (tests used
  the real relay on a configured host) and offers `mail_outbox`. Full suite: 430 passed.
- Not changed: login, refresh, org invites, first-account bootstrap, the in-app admin pages, `/accept-invite` URL.

## 2026-10-22 — moved to AWS, models on Bedrock, a landing page you can use

WhipGuard now runs on the shared AWS VM, and its models are on Amazon Bedrock: Kimi K2.5
for the jury, GLM-5 for the Arbiter and the patch loop, signed with the instance role, so
no model key exists anywhere. The one-client rule held: Bedrock is a provider inside
`azure_client.py`, and the Azure path still works behind a setting. Two things Bedrock
cannot do here are switched off openly rather than faked: web research (no search tool)
and dense retrieval (no embeddings model). The full detection eval passes on the new host,
all six categories, recall and precision both 1.0.

Moving host found three bugs a long-lived server had been hiding: a fresh database could
not boot (the pgvector extension had only ever been created by hand), the jury's 0-100
scale had never reached any model (GPT guessed it and GLM didn't), and `deploy.sh` could
declare success before the new backend existed.

The landing page was rebuilt around things to do rather than read: a simulator where you
pick a bug, choose what goes wrong and make the approval call yourself; a jury lab running
the real disagreement and threshold rules; and three attempts to trick it (plant an
instruction, leak a secret, edit the test that judges the fix), each answered by the
product's own rule.

## 2026-09-21 — installable, mobile-first, and it can reach your phone

WhipGuard is a PWA: installable, standalone, with an offline shell and a service worker
that never caches `/api/`. Push notifications land when a bug is raised, when a fix is ready
for review, and when a verification fails — addressed through `repos.org_id` to everyone in
the owning organization, inside the same dedupe guard the email and Slack paths already use.
Settings carry a per-device toggle that asks for permission directly, plus a test button
that proves the whole path rather than just the browser's permission state.

Every screen was laid out for a phone for the first time. The app had no viewport meta at
all, which meant it had never actually been rendered at that width — so this was a real
layout pass, not a CSS tidy: a bottom tab bar (the header nav was hidden on mobile with
nothing behind it, leaving no navigation whatsoever), tables that become cards, 16px inputs,
44px targets and safe-area insets.

Two bugs in the Fix Council surfaced from running it for real rather than reading it:
`apply_patch` was stripping the mode and owner of every file it wrote, so the sandbox could
not read back a correct patch; and a failed org lookup could relocate an entire repository
tree into the `_unassigned` fallback and back again.

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
