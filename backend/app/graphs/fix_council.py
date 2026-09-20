"""FixCouncilGraph (plan.md §8.2), now parameterized by category (plan.md §2):

START -> RetrievalNode -> PatchGenerationNode -> VerifierNode -> ArbiterNode
      -> conditional: score >= category's resolution threshold?
             yes -> ProposeFixNode(draft PR) -> NotifyNode -> END
             no  -> one bounded retry, then HoldForHumanReviewNode -> END
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, TypedDict

from app import azure_client
from app.categories import CATEGORY_REGISTRY, entry_files_for, scope_excludes
from app.config import settings
from app.detectors import get_detector
from app.integrations import context7_client
from app.prompts import build_prefix, build_volatile_suffix, pad_to_cache_floor, partition_key
from app.sandbox.apply_patch import apply_patch as apply_patch_to_worktree
from app.sandbox.apply_patch import write_file as write_file_in_worktree
from app.routers.ws import emit_event
from app.workspace_map import build_workspace_map

MAX_TOOL_ITERATIONS = 8
REPEAT_NUDGE_THRESHOLD = 3
REPEAT_FAIL_THRESHOLD = 5

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file's current contents from the worktree.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "apply_patch",
            "description": (
                "Replace an exact span of an existing file. This is the preferred way to edit: "
                "old_string must match the file byte for byte, including indentation, and must be "
                "unique unless replace_all is true. Refused if the path is outside this "
                "category's write scope."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_string": {"type": "string", "description": "The exact text to replace, copied from read_file output."},
                    "new_string": {"type": "string", "description": "What to put in its place."},
                    "replace_all": {"type": "boolean", "description": "Replace every occurrence rather than refusing an ambiguous anchor."},
                },
                "required": ["path", "old_string", "new_string"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": (
                "Create a new file, or replace an existing one in full. Prefer apply_patch for "
                "any file that already exists -- a full rewrite can drop code the detector does "
                "not check. Refused if the path is outside this category's write scope."
            ),
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lookup_docs",
            "description": (
                "Look up current, real documentation for a library or framework via Context7 "
                "-- use this before assuming how an API works, especially for anything that "
                "changes across versions. Returns real doc text, not a guess from training data."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "library": {"type": "string", "description": "Library/framework name, e.g. 'express' or 'node:test'."},
                    "topic": {"type": "string", "description": "Optional: narrow the docs to this topic, e.g. 'routing'."},
                },
                "required": ["library"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ask_human",
            "description": (
                "Ask a human a clarifying question at a genuine fork -- two materially "
                "different valid approaches, or a piece of intent the codebase doesn't state "
                "(a hardcoded value that looks like it should be configurable, but the right "
                "default is a product decision). Capped per attempt. Every call must state what "
                "you already considered -- a call with no attempted reasoning is refused."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "options": {"type": "array", "items": {"type": "string"}, "description": "Optional choices."},
                    "already_considered": {"type": "string", "description": "What you tried or thought through before asking."},
                },
                "required": ["question", "already_considered"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish_patch",
            "description": "Call this once you believe the fix is complete and you have run something to check it.",
            "parameters": {
                "type": "object",
                "properties": {"summary": {"type": "string"}},
                "required": ["summary"],
            },
        },
    },
]

MAX_ASKS_PER_ATTEMPT = 2


class FixCouncilState(TypedDict, total=False):
    worktree_path: str
    repo_full_name: str
    repo_id: Any
    category: str
    resolution_threshold: int
    bug_description: str
    touched_files: list[str]
    similar_chunks: list[dict]
    dependents: list[dict]
    # Deterministically assembled context (app/context_broker.py): fused
    # retrieval + located symbols + blast radius + this repo's own prior
    # failures, compiled under one token budget.
    briefing: str
    diff: str
    verifier_result: dict[str, Any]
    score: int
    rubric: list[dict]
    verdict: str
    attempt: int
    prior_rejection: str | None
    issue_id: Any
    base_sha: str
    needs_human: dict | None


def retrieval_node(state: FixCouncilState) -> FixCouncilState:
    """One-hop static import scan from the category's entry file(s) — no
    vector search, no graph DB (plan.md §16). Not hardcoded to one fixture
    file: starts from whichever entry point THIS category's registry row
    names, so a second category needs a config row, not new retrieval code.
    """
    import re

    category = state["category"]
    emit_event({"type": "node", "node": "retrieval", "status": "started", "message": "Scanning one-hop imports…"})
    worktree = Path(state["worktree_path"])
    touched: list[str] = []
    base_sha = state.get("base_sha") or ""
    if not base_sha:
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(worktree), capture_output=True, text=True, timeout=15
        )
        base_sha = (sha.stdout or "").strip()

    for entry in entry_files_for(category, str(worktree)):
        entry_path = worktree / entry
        if not entry_path.exists():
            continue
        touched.append(entry)
        content = entry_path.read_text()
        for match in re.finditer(r"""(?:import .* from ['"](.+?)['"]|require\(['"](.+?)['"]\))""", content):
            imported = match.group(1) or match.group(2)
            if not imported:
                continue
            candidate = (entry_path.parent / imported).resolve()
            try:
                rel = candidate.relative_to(worktree.resolve())
            except ValueError:
                continue
            if candidate.exists():
                touched.append(str(rel))

        for other in entry_path.parent.glob("*.js"):
            if other.name == Path(entry).name:
                continue
            other_content = other.read_text()
            if Path(entry).stem in other_content:
                touched.append(str(other.relative_to(worktree)))

    # Vector similarity over code chunks (plan.md §5.2), ALONGSIDE the static
    # scan above, not instead of it -- the one-hop scan finds what the entry
    # file structurally imports; this finds what's semantically related but
    # not import-connected (a helper in a sibling file the bug description
    # itself points at, a similarly-named function elsewhere in the repo).
    similar_chunks: list[dict] = []
    dependents: list[dict] = []
    briefing = ""
    if state.get("repo_id"):
        from app.graph_index import _extract_symbols, find_dependents
        from app.hybrid_retrieval import hybrid_retrieve

        # Fused retrieval, not dense-only. The lexical and structural
        # channels are what actually answer a bug report's real query -- an
        # exact identifier or a literal assertion string -- which cosine
        # similarity over embeddings only ever matched by accident.
        fused = hybrid_retrieve(
            state.get("bug_description", ""),
            repo_id=state["repo_id"],
            worktree_path=state["worktree_path"],
            extra_terms=list(state.get("touched_files") or []),
        )
        similar_chunks = [
            {
                "file_path": chunk.path,
                "symbol_name": chunk.symbol,
                "content": chunk.text,
                "channels": list(chunk.channels),
                "score": chunk.score,
            }
            for chunk in fused
        ]
        for chunk in similar_chunks:
            if chunk["file_path"] not in touched:
                touched.append(chunk["file_path"])

        # Graph query (plan.md §5.3): what else CALLS a symbol defined in the
        # entry file? Blast radius the static import scan can't see (nothing
        # importing app.js would show up there, but something app.js's own
        # symbols are called BY would matter to "what might this fix break").
        for entry in entry_files_for(category, str(worktree)):
            entry_path = worktree / entry
            if not entry_path.exists():
                continue
            for symbol in _extract_symbols(entry_path.read_text()):
                for dep in find_dependents(state["repo_id"], symbol["name"]):
                    dependents.append(dep)
                    if dep["path"] not in touched:
                        touched.append(dep["path"])

        from app.context_broker import build_briefing

        briefing = build_briefing(
            repo_id=state["repo_id"],
            repo_name=state.get("repo_full_name", ""),
            worktree_path=state["worktree_path"],
            finding_text=state.get("bug_description", ""),
            target_files=sorted(set(touched)),
            prior_feedback=state.get("prior_rejection") or "",
        )

    emit_event({"type": "node", "node": "retrieval", "status": "done", "message": f"Touched files: {', '.join(sorted(set(touched)))}"})
    return {
        **state,
        "touched_files": sorted(set(touched)),
        "similar_chunks": similar_chunks,
        "dependents": dependents,
        "briefing": briefing,
        "base_sha": base_sha,
    }


def _tool_fingerprint(name: str, args: dict) -> str:
    canonical = json.dumps(args, sort_keys=True)
    return hashlib.sha256(f"{name}:{canonical}".encode()).hexdigest()


def patch_generation_node(state: FixCouncilState) -> FixCouncilState:
    category = state["category"]
    emit_event({"type": "node", "node": "patch_generation", "status": "started", "message": "Patch-generation worker (ReAct loop) starting…"})
    worktree = Path(state["worktree_path"])
    workspace_map = build_workspace_map(
        state.get("repo_full_name", ""), state["touched_files"], state["worktree_path"]
    )

    system_prompt = pad_to_cache_floor(
        build_prefix(
            role=(
                f"You are a patch-generation worker fixing a {category} bug. Your tools are "
                "read_file, apply_patch (preferred for any file that already exists), write_file "
                "(new files, or a genuine full replace), lookup_docs, ask_human and finish_patch. "
                "Make the SMALLEST correct change. Do not claim you ran tests: you have no shell. "
                "The verifier re-runs this category's own detector after you call finish_patch, "
                "and that result -- not your summary -- is what decides whether the fix stands."
            ),
            workspace_map=workspace_map,
            category_rules=CATEGORY_REGISTRY[category].rules,
        )
    )
    task = f"Bug: {state.get('bug_description', 'see evidence in the workspace map')}. Fix it."

    # Handed over directly rather than left for a read_file round-trip -- the
    # same "put the answer in the prefix, don't make the model ask for it"
    # reasoning plan.md §9.4 applies to the workspace map itself. The
    # briefing already carries the fused retrieval, the located symbols, the
    # blast radius and this repo's prior failed attempts, all compiled under
    # ONE token budget rather than three independently-chosen slices that
    # never compared their costs against each other.
    briefing = state.get("briefing") or ""
    if briefing:
        task += f"\n\n{briefing}"
    else:
        # Retrieval unavailable (no repo_id, or every channel failed). Fall
        # back to the raw dependents list so the blast radius still reaches
        # the model rather than silently vanishing.
        dependents = state.get("dependents", [])
        if dependents:
            dep_lines = "\n".join(f"- {d['name']} in {d['path']}:{d['line']} ({d['kind']})" for d in dependents)
            task += (
                "\n\nOther code that calls symbols in the file(s) you're touching "
                f"(blast radius -- be careful not to break these):\n{dep_lines}"
            )
    user_prompt = build_volatile_suffix(task, prior_attempt_rejection=state.get("prior_rejection"))

    messages: list[dict] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    call_fingerprints: list[str] = []
    asks_used = 0
    # The prefix is byte-stable across retries by construction (plan.md §9.6),
    # which is exactly what a provider prefix cache keys on. The volatile
    # half sits after it in `input`, where it belongs.
    cache_key = partition_key(state.get("repo_full_name") or "unknown", "patch_worker", system_prompt)

    for _ in range(MAX_TOOL_ITERATIONS):
        turn = azure_client.complete_turn(
            deployment=settings.azure_worker_deployment,
            messages=messages,
            tools=TOOLS,
            role="patch_worker",
            # Every tick, not just the last attempt. A tool loop that cannot
            # think is the failure this whole protocol change exists to end;
            # rationing the thinking to the final attempt would reintroduce it
            # for the seven ticks that actually choose the approach.
            reasoning_effort="medium",
            # Never the Postgres exact-match cache: an identical prompt here
            # means the run is repeating itself, and a cache hit would erase
            # the only evidence the tripwire below has to work with.
            cacheable=False,
            prompt_cache_key=cache_key,
            issue_id=state.get("issue_id"),
        )

        # Replay order matters: reasoning items belong AHEAD of the message
        # they produced, and a function_call item must be in the history
        # before its own function_call_output.
        messages.extend(turn.reasoning_items)
        if turn.content.strip():
            messages.append({"role": "assistant", "content": turn.content})
        for call in turn.tool_calls:
            messages.append({
                "type": "function_call",
                "call_id": call.id,
                "name": call.name,
                # A JSON string, not a dict. The API rejects the dict.
                "arguments": json.dumps(call.arguments),
            })

        if not turn.tool_calls:
            break

        for tool_call in turn.tool_calls:
            name = tool_call.name
            args = tool_call.arguments

            fingerprint = _tool_fingerprint(name, args)
            call_fingerprints.append(fingerprint)
            repeat_streak = sum(1 for f in reversed(call_fingerprints[-REPEAT_FAIL_THRESHOLD:]) if f == fingerprint)

            if repeat_streak >= REPEAT_FAIL_THRESHOLD:
                messages.append({
                    "type": "function_call_output",
                    "call_id": tool_call.id,
                    "output": "Attempt failed: repeated identical tool call too many times.",
                })
                return {**state, "diff": "", "verifier_result": {"error": "loop_tripwire_failed"}}

            if repeat_streak >= REPEAT_NUDGE_THRESHOLD:
                nudge = f" You have called {name} with identical arguments {repeat_streak} times — that will not produce a different result. Change your approach."
            else:
                nudge = ""

            if name == "read_file":
                target = worktree / args.get("path", "")
                content = target.read_text() if target.exists() else f"ERROR: {args.get('path')} does not exist"
                result = content + nudge
            elif name == "apply_patch":
                result = apply_patch_to_worktree(
                    worktree,
                    category,
                    args.get("path", ""),
                    args.get("old_string", ""),
                    args.get("new_string", ""),
                    replace_all=bool(args.get("replace_all")),
                ) + nudge
            elif name == "write_file":
                result = write_file_in_worktree(
                    worktree, category, args.get("path", ""), args.get("content", "")
                ) + nudge
            elif name == "lookup_docs":
                try:
                    result = context7_client.lookup(args["library"], topic=args.get("topic", ""), tokens=1500) + nudge
                except Exception as exc:
                    # Fails closed for the LOOP, not the whole attempt: a
                    # docs-lookup failure is a tool-result the model can react
                    # to (try a different query, proceed without it) — never
                    # a reason to crash patch generation outright.
                    result = f"lookup_docs failed: {exc}" + nudge
            elif name == "ask_human":
                if not args.get("already_considered"):
                    # The symmetric policy to evidence-before-claiming-done
                    # (plan.md §11.6): a call with no attempted reasoning is
                    # refused with a nudge, not answered.
                    result = "REFUSED: state what you already considered before asking." + nudge
                elif asks_used >= MAX_ASKS_PER_ATTEMPT:
                    result = f"REFUSED: already asked {asks_used} question(s) this attempt (cap: {MAX_ASKS_PER_ATTEMPT}). Proceed with your best judgment." + nudge
                else:
                    asks_used += 1
                    question = args["question"]
                    _record_fix_ask(
                        issue_id=state.get("issue_id"),
                        category=category,
                        question=question,
                        options=args.get("options") or [],
                        already_considered=args["already_considered"],
                    )
                    # Pause. A recorded question the model then answers itself
                    # is not a human in the loop. Resume is
                    # worker._handle_resume_human_input -> trigger_fix_council
                    # with the answer as prior_rejection.
                    emit_event({
                        "type": "node", "node": "patch_generation", "status": "done",
                        "message": "Waiting on a human answer",
                    })
                    return {
                        **state,
                        "diff": "",
                        "needs_human": {
                            "question": question,
                            "already_considered": args["already_considered"],
                        },
                    }
            elif name == "finish_patch":
                result = "acknowledged" + nudge
            else:
                result = f"unknown tool {name}"

            messages.append({"type": "function_call_output", "call_id": tool_call.id, "output": result})

        if any(call.name == "finish_patch" for call in turn.tool_calls):
            break

    # git diff reads metadata only — it does not execute the repo's own code, so
    # it runs on the host directly. A worktree's .git file points at an absolute
    # host path for its object store; that path doesn't exist inside the sandbox
    # container (only /work is mounted), which is why running this via
    # run_in_sandbox silently returned an empty diff instead of the real one.
    diff_proc = subprocess.run(
        ["git", "diff"], cwd=str(worktree), capture_output=True, text=True, timeout=30
    )
    diff = diff_proc.stdout

    # Committed here, not left as an uncommitted working-tree change: the
    # approval flow (runner.py / approval_graph.py) only ever pushes and never
    # commits -- found by actually pushing a fix once and hitting GitHub's
    # real "No commits between main and <branch>" 422, because HEAD in the
    # worktree still pointed at the base commit. "The approved artifact is
    # the shipped artifact" (plan.md's own approval-flow principle) means
    # THIS commit, made once at proposal time, is what gets pushed unchanged
    # on approval -- never regenerated, never re-diffed against a moved HEAD.
    if diff:
        subprocess.run(["git", "add", "-A"], cwd=str(worktree), check=True, capture_output=True, text=True)
        subprocess.run(
            [
                "git",
                "-c", "user.name=WhipGuard",
                "-c", "user.email=whipguard@whip-guard.zakarias.in",
                "commit", "-m", f"WhipGuard: fix {category} issue",
            ],
            cwd=str(worktree), check=True, capture_output=True, text=True,
        )

    emit_event({"type": "node", "node": "patch_generation", "status": "done", "message": "Patch produced"})
    return {**state, "diff": diff}


def verifier_node(state: FixCouncilState) -> FixCouncilState:
    """Actually builds/runs the patched worktree using the SAME category
    detector that raised the issue — reports what happened, never an opinion
    about what should happen."""
    category = state["category"]
    emit_event({"type": "node", "node": "verifier", "status": "started", "message": f"Building patched branch, re-running {category} detector…"})
    result = get_detector(category).run(state["worktree_path"])
    emit_event({
        "type": "node", "node": "verifier", "status": "done",
        "message": "Patched branch passes" if not result.failed else "Patched branch still fails",
    })

    if result.failed and state.get("repo_id"):
        # A negative trace, written where the failure actually happens. This
        # is the corpus app/context_broker.py reads back on the NEXT attempt
        # so a later run does not re-propose a patch this one already proved
        # does not work -- and the reason it is written here rather than
        # summarised later is that the failing command and its real output
        # exist at this moment and nowhere else.
        from app.memory_traces import record_trace

        record_trace(
            repo_id=state["repo_id"],
            outcome="failed",
            stage="verify",
            category=category,
            attempt=int(state.get("attempt") or 1),
            failing_command=f"{category} detector re-run on the patched branch",
            touched_paths=list(state.get("touched_files") or []),
            detail=(result.assertion_text or "")[-1500:],
        )

    return {
        **state,
        "verifier_result": {"passes": not result.failed, "output": result.assertion_text},
    }


def arbiter_node(state: FixCouncilState) -> FixCouncilState:
    category = state["category"]
    if not state["verifier_result"].get("passes"):
        emit_event({"type": "node", "node": "arbiter", "status": "done", "message": "Score 0 — verifier failed"})
        return {
            **state,
            "score": 0,
            "rubric": [{"factor": "verifier", "weight": 0, "note": "The category's own check still fails after the patch."}],
            "verdict": "Patch does not resolve the original failing assertion.",
        }

    prefix = pad_to_cache_floor(
        build_prefix(
            role="You are the Arbiter scoring a proposed FIX. Score 0-100 given the diff and the Verifier's real test result.",
            workspace_map=build_workspace_map(
                state.get("repo_full_name", ""), state["touched_files"], state.get("worktree_path", "")
            ),
            category_rules=CATEGORY_REGISTRY[category].rules,
        )
    )
    suffix = build_volatile_suffix(
        f"Diff:\n{state['diff']}\n\nVerifier: tests pass = {state['verifier_result']['passes']}"
    )
    emit_event({"type": "node", "node": "arbiter", "status": "started", "message": "Arbiter scoring the fix…"})
    verdict = azure_client.call_arbiter(prefix, suffix, repo_full_name=state.get("repo_full_name", ""))
    emit_event({"type": "node", "node": "arbiter", "status": "done", "message": f"Resolution confidence: {verdict.score}/100"})
    return {
        **state,
        "score": verdict.score,
        "rubric": [f.model_dump() for f in verdict.factors],
        "verdict": verdict.verdict,
    }


def route_on_resolution_score(state: FixCouncilState) -> str:
    threshold = state.get("resolution_threshold", CATEGORY_REGISTRY[state["category"]].resolution_threshold)
    if state["score"] >= threshold:
        return "propose"
    return "retry" if state.get("attempt", 1) < 2 else "hold"


def route_after_patch(state: FixCouncilState) -> str:
    if state.get("needs_human"):
        return "ask"
    return "verify"


def retry_prepare_node(state: FixCouncilState) -> FixCouncilState:
    """Second attempt: reset the worktree to the SHA retrieval captured, keep
    the byte-identical system prefix (workspace_map + rules), and put the
    rejection reason in the volatile suffix only."""
    worktree = Path(state["worktree_path"])
    base = state.get("base_sha") or ""
    if base:
        subprocess.run(
            ["git", "reset", "--hard", base],
            cwd=str(worktree), capture_output=True, text=True, timeout=30,
        )
        subprocess.run(
            ["git", "clean", "-fd"],
            cwd=str(worktree), capture_output=True, text=True, timeout=30,
        )
    emit_event({
        "type": "node", "node": "retry", "status": "started",
        "message": "Retrying the patch with the previous verdict as rejection",
    })
    return {
        **state,
        "attempt": int(state.get("attempt") or 1) + 1,
        "prior_rejection": state.get("verdict") or "below resolution threshold",
        "diff": "",
        "score": 0,
        "verifier_result": {},
        "needs_human": None,
    }


def _record_fix_ask(*, issue_id, category: str, question: str, options: list, already_considered: str) -> None:
    """Sync write — patch generation is not an async node."""
    from app import sync_db

    option_rows = [{"id": str(i), "label": o} for i, o in enumerate(options)]
    with sync_db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO human_input_requests
                (id, issue_id, node_name, kind, question, options, context, status, thread, created_at)
            VALUES (
                gen_random_uuid(), %s, 'fix_council.patch_generation',
                %s, %s, %s::jsonb, %s::jsonb, 'pending', %s::jsonb, now()
            )
            """,
            (
                str(issue_id) if issue_id else None,
                "single_select" if option_rows else "free_text",
                question,
                json.dumps(option_rows),
                json.dumps({"already_considered": already_considered, "category": category}),
                json.dumps([{"from": "agent", "text": question, "at": None}]),
            ),
        )
        conn.commit()


def build_fix_council_graph():
    from langgraph.graph import END, StateGraph

    graph = StateGraph(FixCouncilState)
    graph.add_node("retrieval", retrieval_node)
    graph.add_node("patch_generation", patch_generation_node)
    graph.add_node("verifier", verifier_node)
    graph.add_node("arbiter", arbiter_node)
    graph.add_node("retry_prepare", retry_prepare_node)

    graph.set_entry_point("retrieval")
    graph.add_edge("retrieval", "patch_generation")
    graph.add_conditional_edges(
        "patch_generation", route_after_patch, {"verify": "verifier", "ask": END}
    )
    graph.add_edge("verifier", "arbiter")
    graph.add_conditional_edges(
        "arbiter", route_on_resolution_score, {"propose": END, "retry": "retry_prepare", "hold": END}
    )
    graph.add_edge("retry_prepare", "patch_generation")
    return graph.compile()


def run_fix_council(
    worktree_path: str,
    category: str = "ui",
    repo_full_name: str = "",
    repo_id: Any = None,
    issue_id: Any = None,
    bug_description: str = "",
    resolution_threshold: int | None = None,
    attempt: int = 1,
    prior_rejection: str | None = None,
) -> FixCouncilState:
    graph = build_fix_council_graph()
    return graph.invoke(
        {
            "worktree_path": worktree_path,
            "category": category,
            "repo_full_name": repo_full_name,
            "repo_id": repo_id,
            "issue_id": issue_id,
            "bug_description": bug_description,
            "resolution_threshold": resolution_threshold or CATEGORY_REGISTRY[category].resolution_threshold,
            "attempt": attempt,
            "prior_rejection": prior_rejection,
        }
    )
