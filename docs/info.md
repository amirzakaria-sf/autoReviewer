# Two-agent protocol (Cursor + Claude) — WhipGuard

**Read this file at the start of every session, before writing code.**

Two agents touch this repo: **Cursor** and **Claude**. They share this box and this
checkout, but **not a chat and not a session**: everything one knows about the other's
work comes from the files below. If you skip this protocol, the other agent will overwrite
your work, re-implement something already shipped, or ship a claim nobody checked.

This repo is one checkout (`repoFixer/`, remote `autoReviewer`) holding `backend/`
(FastAPI + LangGraph councils), `frontend/` (Next.js App Router), `docker-compose.yml` and
`deploy.sh`. Live at **https://whip-guard.zakarias.in**.

---

## 0. Identity and where you are running

**Both agents run on this server.** They share the checkout, the `.env`, the database, the
running containers and `./deploy.sh`.

| If you are… | Your folder | Your change log |
|-------------|-------------|-----------------|
| Cursor | `docs/cursor/` | `docs/cursor/changes.md` |
| Claude | `docs/claude/` | `docs/claude/changes.md` |

Both of you can edit code, run `pytest` and `tsc`, read the real `.env`, reach Azure,
GitHub, Slack and Cloudflare, query production Postgres, exercise a flow end to end, and
read container logs. What follows from that:

- **Neither agent may say "I could not verify this at runtime."** You can. If you ship a
  runtime claim, run it, and put the command and its output in your log. **NOT verified**
  still exists and is still honest — but it means *you chose not to*, so say why.
- **Deploying is a policy, not a capability.** Either agent technically can. Only one
  should own a given slice, it goes in `STATUS.md`, and neither deploys without the user
  asking.
- **You still do not share a chat.** Everything the other agent knows about your session
  comes from STATUS, DECISIONS and your `changes.md`. That is the whole reason for §4.

You **may** read the other agent's folder. You **must not** write to it.

---

## 0b. Git is how work reaches the next session (and production)

You share a filesystem, but not a chat and not a session. An unpushed commit is invisible
to the other agent's next session and to a rebuilt checkout.

- **Start of session:** `git pull --ff-only`. If that fails, stop and tell the user — do
  not merge or rebase around the other agent's work on your own.
- **End of a session that changed anything:** commit, then **`git push`**.
- Never push `.env` or secrets. `.gitignore` covers `.env`; keep it that way.

`deploy.sh` rebuilds from the **working tree**, not from `origin/main` — so on this repo a
commit is how the other agent sees your work, and a deploy is a separate act. Do not
assume one implies the other. Whichever you do, say which in STATUS.

---

## 1. What is shared vs what is yours

**Do not copy architecture into both agent folders.** Two architecture files will diverge
within a day. That is the failure mode this layout exists to prevent.

### Canonical (one copy, both agents update)

| File | Role |
|------|------|
| `README.md` (repo root) | **Source of truth for what ships today.** If this and the code disagree, **code wins**, then you update this file in the same change. |
| `plan.md` (repo root) | The original design essay — the *why*. Historical. Do not update it per-change; do not treat it as current behaviour. |
| `docs/ARCHITECTURE.md` | Short module / route / tenancy map. Keep it short. |
| `docs/CHANGELOG.md` | Short product history. Append a dated heading when a slice lands. |
| `docs/STATUS.md` | **Living handoff.** Who last worked, what is in flight, what the next agent must not touch. Update every session that changes code. |
| `docs/DECISIONS.md` | Why we chose X over Y. Add a row when you make a lasting choice. Do not re-litigate a row without the user asking. |
| `docs/specs/` | **Approved** product specs (ready for both agents). |
| `docs/plans/` | **Accepted** plans currently being executed. Finished plans go to `docs/archive/`. |
| `docs/archive/` | Historical diaries and shipped plans. Not live work. |
| `AGENTS.md` | Harness rules (the security split, tenancy, forbidden actions). |

### Yours only (drafts and a diary)

```
docs/<you>/
  README.md           why this folder exists
  changes.md          append-only log of what YOU did (required every session you edit)
  notes.md            scratch, open questions, "tell the other agent…"
  architecture.md     exploration notes to merge into docs/ARCHITECTURE.md — never a second source of truth
  plans/              plans you drafted that are not accepted yet
  specs/              spec drafts that are not approved yet
```

When a plan or spec is accepted by the user, **move** it to `docs/plans/` or `docs/specs/`
and leave a one-line pointer in your folder. Do not keep two full copies.

---

## 2. Session start (mandatory)

Do this in order. Do not start implementing until 1–5 are done.

0. **`git pull --ff-only`.** Everything below is stale otherwise.
1. Read **`docs/STATUS.md`**. If it says the other agent has in-flight work on files you
   need, **stop** and tell the user. Do not "just continue."
2. Read the **other** agent's `changes.md` — last **3** entries only, unless STATUS points
   at a specific older entry.
3. Read **`docs/DECISIONS.md`** if your task could contradict a prior choice.
4. For the feature you are changing, skim the relevant part of **`README.md`** and
   **`docs/ARCHITECTURE.md`**.
5. Then read the code. Docs can be stale; code is not optional.

If STATUS is empty or clearly stale, say so in your first reply and then fill it in when
you finish.

---

## 3. While you work

- Prefer **small, complete slices** over a half-finished epic the other agent cannot pick
  up.
- **Code has no owner.** Either agent may edit any file, including one the other wrote —
  that is how a gap gets closed. Only `docs/<agent>/` is owned. If you change the other
  agent's design, say so in your log, and add a `DECISIONS.md` row if the change is
  lasting. The one exception: do not rewrite a file STATUS lists as in flight.
- Do not invent a parallel abstraction next to an existing one (`azure_client`,
  `work_queue`, `visible_repo_ids`, `get_detector`, `resolve_approval`).
- **When a library's behaviour is load-bearing, read the library.** `.venv/` and
  `node_modules/` are on disk and readable. Guessing what SQLAlchemy, LangGraph or Next
  does with your callback has already produced dead code in sibling repos.
- Rules in `AGENTS.md` are not negotiable unless the user explicitly changes them.
- Secrets never belong in `docs/` (no `.env` values, no Azure/GitHub/Slack/Cloudflare
  keys), and never in a finding's evidence — see §11.

---

## 4. Session end (mandatory if you changed anything)

Same turn as the code, not "later."

1. **`docs/STATUS.md`** — overwrite the top block: date, agent, what shipped, what is still
   open, files still hot, what the other agent should do next (or "nothing in flight").
   Put `Commit: pending` until step 6.
2. **Your `changes.md`** — append one block (template in §5). Newest entry at the **top**.
   Same `Commit: pending`.
3. **Shared docs** if behaviour changed:
   - user-visible or deploy behaviour → `README.md` and/or `docs/ARCHITECTURE.md`
   - a shipped slice → short dated note in `docs/CHANGELOG.md`
   - a lasting choice → `docs/DECISIONS.md`
4. **`docs/<you>/notes.md`** — only if there is something the other agent needs that does
   not belong in STATUS (e.g. a trap you hit).
5. Do **not** dump a second full architecture into your folder.
6. **Commit** (standing order — do not wait for the user to say "commit"):
   - `git add` only the files you meant to change. Never `.env`, never secrets, never
     `node_modules`, never `frontend/tsconfig.tsbuildinfo`.
   - Commit with a short message that says *why*. Do not skip hooks.
   - `git rev-parse HEAD` (full) and `--short`.
   - Replace `pending` with the **full hash** in `STATUS.md` and your latest `changes.md`
     block.
   - Second commit: `docs: record <short-hash> in agent log` — only those two files.

   **Which hashes get recorded:** every commit that changes the **codebase** gets its hash
   written into `STATUS.md` and `changes.md`. The follow-up commit that only touches the
   sync docs does **not** need its own hash recorded — what the other agent needs to
   `git show` is the code, and a docs-only commit is already the thing they are reading.
7. **`git push`.**

If you changed code or docs, the log **and** the commit are required even for a one-line
fix.

**Reviews count as work.** If the user asked you to audit or review the other agent's
changes, log it (`**Type:** review`) and commit it even though no code changed — otherwise
the findings die with the chat and the other agent re-derives them. Only a session where
you read the repo *and were asked nothing* skips the log.

The user may still say "don't commit this" for a specific turn. That overrides auto-commit
for that turn only.

---

## 4a. Who verifies what

Both agents can reach the database, Azure, GitHub, Slack, Cloudflare and the running app,
so verification is not split by capability. It is split by **who is making the claim**.

**If you shipped it, you ran it.** A runtime claim in your log carries the command and what
it returned — not "confirmed", not "should work". `pytest` passing is a verified claim
about the tests, and nothing more. "Approval deploys a preview" is verified only if you
approved something and loaded the URL.

**"No exception raised" is not verification.** This repo has shipped, more than once, code
that imported, typechecked, passed tests and could never work: a worker running a stale
image for three deploys, a Slack integration whose guards silently skipped every post, a
freshness check comparing against a ref that does not exist in a mirror clone, and an
outcome checker asserting `reachable: True` without a request. Each was invisible until
someone ran the real thing. See §11.

**NOT verified** is still the honest answer for anything you did not exercise — say why,
and say what would prove it. "Approve a fix and watch for a 200 on the preview URL" is
useful. "Needs testing" is not.

Put anything you left unexercised in `docs/STATUS.md` under **Needs verification** so the
next session can pick it up. Clear it when you run it, or replace it with what you found.

---

## 4b. Schema changes

This deployment builds its schema at boot: `Base.metadata.create_all` plus
`app/schema_sync.py`, which adds model-declared **columns** and **enum labels** the
database is missing. Alembic is scaffolded (`backend/alembic/`) but is **not** wired into
`deploy.sh` — `schema_sync` is the live path.

- **Additive changes need no migration.** Add the column to the model with a
  `server_default` where it is NOT NULL, and `schema_sync` applies it on the next boot.
- **A destructive change — drop, rename, narrow a column with data — needs a
  `DECISIONS.md` row first, not after, and a real Alembic revision.** `schema_sync` will
  not do it and must not be taught to guess.
- Put it in STATUS before you start: `Schema change in flight: <what> (<agent>)`.
- Never edit a revision that has been applied anywhere.

---

## 5. Change-log block (copy this)

Use this shape in `docs/cursor/changes.md` or `docs/claude/changes.md`:

```markdown
## YYYY-MM-DD HH:MM (timezone) — short title

- **Agent:** cursor | claude
- **Commit:** pending   <!-- replace with full SHA after git commit -->
- **User ask:** one sentence
- **Shipped:** bullets of what landed (paths)
- **Docs updated:** list of shared files you edited, or "none"
- **Verified:** the commands you actually ran and what they returned
- **NOT verified:** what you could not exercise, and what would prove it
- **Not done / watch-outs:** anything the other agent will trip on
- **Hot files:** paths still in flux, or "none"
```

Add `- **Type:** review` when the session produced findings rather than code.

`STATUS.md` Current block must also include `- **Commit:** <full SHA>`.

Use the user's local date. If you do not know the time, the date is enough.

---

## 6. Conflict rules

1. **Code** is the source of truth for behaviour.
2. Then **`README.md`**.
3. Then **`docs/ARCHITECTURE.md`**.
4. `plan.md` is the design essay, not current behaviour.
5. Agent `architecture.md` files are notes, never authority.
6. If you and the other agent's last STATUS disagree, **stop and ask the user**.

---

## 7. Plans and specs

| State | Where it lives |
|-------|----------------|
| You are drafting | `docs/<you>/plans/` or `docs/<you>/specs/` |
| User approved; either agent may implement | Move to `docs/plans/` or `docs/specs/` |
| Shipped | Keep the spec; mark status `shipped` at the top; add CHANGELOG + README |

A plan that stays forever in `docs/cursor/plans/` is invisible to Claude. Move it when it
is real work.

---

## 8. What else is maintained here (and why)

| Extra file | Why |
|------------|-----|
| `STATUS.md` | Chat history is not shared. This is the handoff. |
| `DECISIONS.md` | Stops both agents re-arguing the security split, tenancy, or the approval gate. |
| Agent `notes.md` | Informal "I already tried X" so the other agent does not repeat it. |
| Agent `architecture.md` | Scratch while exploring; merge then delete. |

Do **not** add a second changelog at repo root. Do **not** put `.env.example` copies in
agent folders.

---

## 9. Harness boot files

Cursor loads `AGENTS.md` automatically. That file must keep a pointer at **this** protocol.

Claude Code loads `CLAUDE.md`. It exists and contains a pointer to this file and the
architecture map, and nothing else. **Keep it that way** — rules duplicated there will
drift out of step with this file within a week, and then neither agent knows which copy is
real.

---

## 10. Do not

- Do not write the same architecture twice.
- Do not clear or rewrite the other agent's `changes.md`.
- Do not mark work "in flight" in STATUS and then leave it for days without a note.
- Do not treat `plan.md` as the live product spec; `README.md` is.
- Do not skip the auto-commit after a session that changed files, unless the user said not
  to commit this turn.
- Do not commit `.env`, secrets, or `frontend/tsconfig.tsbuildinfo` (a build artifact that
  shows up dirty on every build).
- Do not claim a runtime behaviour works if you did not run it. Put it in **NOT verified**.
- Do not deploy without the user asking.

---

## 11. WhipGuard-specific traps

Every one of these cost a real debugging session. They are here so the second agent does
not pay for them again.

### One Azure client, and it speaks Responses

`app/azure_client.py::complete_turn` is the only place a generation call is made.
`app/embeddings.py` is the one exception — a different API on a different client.

Constructing your own `AzureOpenAI` anywhere else is the trap, and it is not hypothetical:
`fix_council.py` and `counsel/agent.py` each did for months. Nothing broke loudly. What
happened instead is that the two most expensive call sites in the product recorded no usage
at all, and the patch loop ran on Chat Completions — which on GPT-5.x cannot combine tools
with a reasoning effort, so an eight-tick agentic loop had never once thought.

Callers pass **Responses input items**, never Chat messages. Three shapes have each cost a
project a production 400, and all three are invisible against a mock that shrugs:

- a tool result is `{"type": "function_call_output", "call_id", "output"}`, **not**
  `{"role": "tool"}`
- `function_call.arguments` is a JSON **string**, not a dict
- reasoning items are replayed with `id` and `summary` only — never a guessed extra field,
  and never dropped, since they belong ahead of the message they produced

A `400` drops the one optional parameter its body names and retries once. It never means
"the Responses API is unsupported" — treating it that way is how a sibling app swallowed
content-filter rejections for weeks while appearing healthy.

### Anything the model writes goes through `apply_patch`, scoped

`app/sandbox/apply_patch.py` holds both write primitives, and both call
`scope_excludes(category, path)` **inside the primitive**. Do not add a second scope helper
and do not check scope at a call site — that is how one call site ends up unguarded.
`scope_glob` is an **exclusion** set: inverting it lets a UI patch rewrite the Playwright
spec it is about to be judged by.

### Web content is untrusted, exactly like repository content

`app/research.py` reads the live web. A page can contain text shaped like an instruction; it
is data reporting that it says that. Both research roles are told so, and so is Counsel about
repo content. A claim is only usable when it is attributed to a URL the search **actually
returned** — a model will otherwise supply a plausible one from memory, which is
indistinguishable from a real citation to every reader downstream.

### The security split is the architecture, not a preference

`backend/app/main.py`'s process renders model output and untrusted repository text. It has
**no** `/var/run/docker.sock`, and its workspace mount is read-only. Everything privileged
— cloning, patching, running a detector in a sandbox, pushing, deploying — is queued into
`work_items` and executed by `backend/app/worker.py`, which has the socket and no inbound
port.

- **Never add a Docker call, a `git push`, or a sandbox run to a router.** Queue it
  (`app/work_queue.py`, add a kind to `KINDS`) and handle it in `worker.py`.
- **Never mount the Docker socket into the web or frontend container** to make a feature
  easier. That is root-equivalent control of the host from a process that renders
  model-authored content.

### The worker shares the backend's image tag

`docker-compose.yml`'s `worker` service has **no `build:`** — it uses
`image: whipguard-backend:latest`. When it had its own `build:`, Compose gave it a separate
image, so `docker compose build backend` left the worker on old code. **Three deploys ran
stale code with no visible error**: containers healthy, queue draining, the new code simply
absent. Do not give the worker its own build stanza.

### Tenancy: one column, one dependency

`repos.org_id` is the **only** column carrying tenancy. Issues, fixes, traces, chunks and
symbols reach their org *through* their repo. Every product read and every action scopes
through `app/deps.py`'s `visible_repo_ids`.

- A repo outside the caller's org is **404, never 403**. A 403 confirms the id names
  something real, which makes the boundary a lookup service for the other side of it.
- This was missed once and shipped: a developer invited into a brand-new org with no
  repositories opened the dashboard and saw another org's findings, including file paths
  and leaked-credential detail.
- **Known open gaps** — see STATUS: `routers/counsel.py`, `routers/ws.py` and
  `routers/human_input.py` are still unscoped.

### Forbidden actions

- **WhipGuard never merges a PR.** There is no merge capability and there must not be one.
  A human merging on GitHub is a real resolution, synced back by the `pull_request`
  webhook — that is a different thing.
- **Nothing reaches GitHub until a human approves.** A proposal is local: the diff is
  stored on `Fix.diff` and reviewed in the dashboard. `resolve_approval` is what pushes the
  branch and opens the PR. Rejecting leaves the repository untouched.
- **Never fall back to `settings.fixture_repo`.** Eight separate bugs came from it — a Fix
  Council that cloned the fixture for every issue, a poller that polled only the fixture, a
  PR opened against the wrong repository. If the repo cannot be resolved, log and refuse.

### Git in a mirror clone

The workspace is `<org-slug>/<owner>__<repo>/{mirror,counsel,fixes/<n>-<slug>}` under
`WORKSPACE_HOST_ROOT` on the data disk, bind-mounted at the **same absolute path**
(`/srv/workspace`) on the host and inside the containers — because a git worktree records
its link to the mirror as an absolute path, and under any other name every worktree
resolves from inside the container and nowhere else.

- **`origin/main` does not resolve in a mirror clone.** A `--mirror` maps every ref into
  `refs/heads`, so `git merge-base --is-ancestor origin/main HEAD` exits **128**, not 1.
  Use the bare branch name, and distinguish exit 0 / 1 / other — collapsing them made every
  approval re-run the whole suite and abort before pushing.
- **The authenticated remote is `https://x-access-token:<token>@github.com/…`.** Written
  as `https://<token>@…`, git reads the token as a *username* and asks for a password;
  cloning a public repo still succeeds, so it only fails at the first push, as
  `could not read Password`, naming no URL.
- `git config --global --add safe.directory '*'` is in the Dockerfile because the container
  runs as root over a uid-1000 workspace; without it every attribution tool silently
  returned "no git history".

### Verification gates

- Both the pre-push re-verify and the post-deploy oracle call **this issue's category
  detector** (`app/detectors/get_detector`), not a hardcoded Playwright run. The detector
  interface takes `base_url` so a browser-driven category can be pointed at the live
  preview; the static scanners ignore it and say so.
- The cross-app outcome checker fails the run closed on **any** disagreement. An
  **unconfigured** surface is not a disagreement — Slack being disconnected must read as
  absent, not contradictory, or every deploy fails for anyone without Slack.

### Secrets never appear in evidence

The secret scanner reports `path:line: label` and never the matched value. A finding's
evidence is copied into a GitHub issue body, the dashboard and Slack — a leaked-credential
detector that republishes the credential is worse than none.

### Tests

- `backend/tests/conftest.py` rewrites `DATABASE_URL` to `whipguard_test` **before**
  `app.config` is imported, and drops/recreates that database each session. Tests used to
  run against production and left fixture rows showing on the dashboard as real issues.
- Run them with `backend/.venv/bin/python -m pytest -q` from `backend/`.
- `CREATE DATABASE … TEMPLATE template0` — this server's `template1` has a glibc collation
  mismatch.

### Ports

Postgres `127.0.0.1:5433`, backend `127.0.0.1:8300`, frontend `127.0.0.1:3300`, all bound
to loopback with TLS terminating at the host nginx (`whip-guard.zakarias.in`). The host
nginx config is shared with two sibling products — see `DECISIONS.md` on the single `map`
directive before touching it.
