# Azure Responses + apply_patch + curated web research

**Status:** shipped 2026-09-20 · **Author of the plan:** user · **Implementer:** claude
**Commits:** `6093ea8` (Responses + apply_patch), see `CHANGELOG.md` for the research slice.

The plan as accepted is below, with the places live probing contradicted it marked
**[deviation]**. A plan file that quietly matches the code it produced teaches nothing; the
deviations are the part worth reading.

---

## 1. Goal

Make the patch worker, jury, Arbiter, Counsel and the PRD council talk to Azure the way
`opencode` already does: Responses API first, native reasoning, flat tools, structured
output, `prompt_cache_key`. Replace full-file `write_file` as the default edit with an
exact-anchor `apply_patch`.

The product does not become a code generator. The judge of a fix is still the category
detector, twice — in the sandbox, then against the live preview. This slice only lets the
model that writes the diff think, and makes that diff a surgical replace.

## 2. Why

- Patch generation used Chat Completions + tools on GPT-5.x. That combination cannot carry
  native reasoning, so the eight-tick loop had reasoning switched off for its whole life.
- `fix_council.py` and `counsel/agent.py` each built their own `AzureOpenAI` and recorded
  no usage, which made the **One Azure call site** decision untrue in practice.
- `write_file` overwrote whole files. On the fixture that is harmless; on a real repository
  it is how a patch passes the detector and breaks a sibling function.
- Jury and Arbiter JSON was asked for in prose. GPT-5.x is unreliable at that.
- `partition_key()` was implemented, tested, and never sent.

## 3. What was verified live before any code was written

Against `gpt-5.6-terra` and `gpt-5.6-luna`, `{endpoint}/openai/v1/` with
`api_version="preview"`:

| Probe | Result |
|---|---|
| Responses + `reasoning: {effort}` , no tools | 200. Trivial prompts spend 0 reasoning tokens; a real one spends hundreds. |
| Responses + reasoning + flat tool, `strict: false` | 200, `function_call` returned. Tools and reasoning coexist. |
| `prompt_cache_key` | Accepted. |
| Built-in `{"type": "web_search"}` | 200. Output is `['reasoning', 'web_search_call', 'message']`, with `url_citation` annotations and the queries the model chose. |

**[deviation] The SDK pin.** The plan said `openai>=1.58.1,<2.0.0` (the sibling project's
pin). Installed here is **3.13.0**, and `prompt_cache_key` is in the real signature of
`responses.create`. The cap would have been a two-major downgrade. Shipped as
`openai>=1.58.1` with no upper bound.

**[deviation] The web-research transport.** The plan pointed at `opencode`'s Azure AI
Foundry agent. That agent (`web-research:1`) exists and is enabled, but its definition
names model `gpt-5.2-chat`, which no longer exists on that resource — every call returns
404 `DeploymentNotFound`, confirmed across four invocation shapes. Its entire definition is
one `{"type": "web_search"}` tool, which works directly on our own deployments. Shipped
native; no second endpoint, no second key, nothing to repair in a portal.

## 4. Architecture

`app/azure_client.py` is the one generation client. `complete_turn(...)` returns a
`ModelTurn`: content, tool calls, reasoning items, token counts, protocol, plus citations
and search queries when the built-in search ran.

Callers speak **Responses input items** and nothing else. The Chat Completions shape exists
only past the fallback boundary, in `_to_chat_messages`.

Three shapes are not negotiable, each having cost a sibling project an incident:

- Full history every turn. No `previous_response_id`.
- Reasoning items replayed with `id` and `summary` only.
- `function_call.arguments` is a JSON **string**; a tool result is a `function_call_output`
  item, never `{"role": "tool"}`.

### Reasoning effort

| Caller | Deployment | Effort |
|---|---|---|
| Patch ReAct, every tick | worker | medium |
| Counsel | worker | medium |
| Arbiter / meta-auditor | planner | medium |
| Corroborator | worker | medium |
| Skeptic / fix-skeptic / verifier | fast / worker | low |
| Research gatherer and curator | worker | medium |

### Fail-closed 400s

Drop the one optional parameter the body actually names (`prompt_cache_key` first, then
`reasoning`), retry once, remember it for the process. Anything else raises with the body
logged. A blanket fallback is how sibling apps swallowed content-filter rejections for
weeks while appearing to work.

`WHIPGUARD_AZURE_API=chat` is an operator hatch, not a failure path.

## 5. apply_patch

`app/sandbox/apply_patch.py`. Pure filesystem, no Docker, not in a router. Refuses: a path
escaping the worktree, a missing file, an empty anchor, an identical replacement, zero
matches, and more than one match without `replace_all`. Writes through a temp file in the
same directory, then renames.

Both `apply_patch` and `write_file` are gated by the same `scope_excludes(category, path)`
— inside the primitive, so a future call site cannot forget it. `scope_glob` is an
**exclusion** set; inverting it would let a UI patch rewrite the Playwright spec it is
being judged by.

## 6. Web research

`app/research.py` — two roles, for the same reason the Bug Council has a Skeptic:

- **Gatherer** runs the model's own `web_search` and reports claims with sources.
- **Curator** attributes every claim to a URL the search **actually returned**, scores
  relevance / recency / authority, and drops the rest.

A claim whose URL was not among the returned sources is dropped mechanically, whatever the
curator said — a model can produce a plausible URL from memory, and that is precisely the
failure curation exists to catch. Rejections are persisted next to keeps in
`research_findings`: "we looked and chose not to use it" is a different state from "we
never looked".

Bound in three places, all autonomous (no keyword lists — a keyword list cannot know about
the service someone integrates next week):

- **PRD council** — a planner decides per requirement what needs looking up, capped by
  `WEB_RESEARCH_MAX_CALLS_PER_RUN`. Findings reach the Drafter as cited evidence and the
  document as a "What the web says" section.
- **Counsel** — `research_web`, a read tool.
- **Patch worker** — `research_web`, its own bounded sub-call so a search cannot eat the
  eight ticks the loop has to fix something.

Web pages are untrusted content, stated to both roles.

## 7. Out of scope, deliberately

The three tenancy gaps (`counsel.py`, `ws.py`, `human_input.py`) stay open and stay listed
in `STATUS.md`. Mixing them into this diff makes an un-reviewable PR. Also untouched:
`embeddings.py`, the worker's image tag, `approval_graph.py`, the outcome checker, the
detectors, and `plan.md`.

Not deployed. Policy: the user asks.
