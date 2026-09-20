# CLAUDE.md

**Read [`docs/info.md`](docs/info.md) first.** It is the two-agent protocol (Cursor +
Claude): session start, what "verified" means, where the handoff lives
([`docs/STATUS.md`](docs/STATUS.md)), and §11 — the WhipGuard traps that have each already
cost a debugging session.

Then:

- Harness and product rules: [`AGENTS.md`](AGENTS.md)
- What ships today: [`README.md`](README.md) — source of truth, after the code
- Module / router / tenancy map: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
- Why we chose X over Y: [`docs/DECISIONS.md`](docs/DECISIONS.md)
- The original design essay: [`plan.md`](plan.md) — the *why*, historical, not current
  behaviour

Nothing else belongs in this file. Rules duplicated here drift out of step with
`docs/info.md` within a week, and then neither agent knows which copy is real.
