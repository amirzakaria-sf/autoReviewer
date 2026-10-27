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

- **As of:** 2026-10-27
- **Last agent:** claude
- **Commit:** `0a4b13aac7a15148cbc3efd8f4e60a83ac2e3b44` (`0a4b13a`)
- **Deployed:** unchanged from the 2026-10-22 block (Bedrock, `3b58fdc` plus `1c2ecf7`/`f36ab2b`/`34e5245` as deployed by
  whoever deployed them). This session did not deploy (owner: code + commit + push only).
- **User ask:** sync LLM providers across the five apps (`~/ai/llmIntegrations.md`); WhipGuard had neither NVIDIA nor
  OpenRouter.
- **Shipped:** `LLM_PROVIDER=nvidia|openrouter` as Chat Completions gateways (`app/azure_client.py::_gateway_turn`), per-role
  models resolved in `app/config.py::_gateway_role_models`, `LLM_PROVIDER` validated (`Literal`, case-insensitive),
  `ProviderError(kind, provider)` for classified gateway failures (incl. mantle's account-wide `Error 002`). Bedrock path
  unchanged apart from the rename `_bedrock_turn` → `_gateway_turn`.
- **Live state found:** Bedrock mantle refuses this AWS account for every model (`400 validation_error` "Error 002: Access to
  Bedrock models is not allowed for this account", 2026-10-27, host instance role). So every council run fails in
  production today; Azure is unconfigured. Switching: `LLM_PROVIDER=nvidia` + `NVIDIA_API_KEY` in `.env`, then recreate
  `backend` and `worker`.

### Needs verification

- A live NVIDIA (or OpenRouter) council: switch as above, run a scan, expect `council_runs` rows with `protocol: nvidia` and a
  forced-function `JurorOpinion` / `ArbiterVerdict`. Whether Nemotron honours a forced `tool_choice` is untested live (the
  JSON-envelope retry covers it if not).
- Everything in the 2026-10-22 "Needs verification" list (GitHub identity, Cloudflare Pages token, Slack, push on a device).

### Other agent should

- **Hot files:** none.
- Read §11 "One model client, four providers" in `docs/info.md` before touching `azure_client.py` or the role settings.

---

## Recent (newest first)

- **2026-10-22 (claude, `3b58fdc`)** — moved to the AWS host `13.60.61.135`, models on Bedrock mantle (Kimi K2.5 fast,
  GLM-5 worker/planner), web research and dense retrieval off on Bedrock, three latent bugs fixed (`vector` extension,
  `_JUROR_RUBRIC` never wired, `deploy.sh` health wait). Blocked on the user then and still: a GitHub credential, a
  Cloudflare Pages token, Slack tokens.

- **2026-09-21 (claude, `17460b2`)** — Azure Responses, `apply_patch`, the research
  council, the tenancy boundary closed, PWA + push + mobile. Fix Council ran end to end on
  issue #21 at resolution 100/100 on the old host.

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
