"""FixCouncilGraph (plan.md §8.2):

START -> RetrievalNode -> PatchGenerationNode -> VerifierNode -> ArbiterNode
      -> conditional: score >= threshold?
             yes -> ProposeFixNode(draft PR) -> NotifyNode -> END
             no  -> one bounded retry, then HoldForHumanReviewNode -> END
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any, TypedDict

from openai import AzureOpenAI

from app import azure_client
from app.category_rules import UI_CATEGORY_RULES
from app.config import settings
from app.prompts import build_prefix, build_volatile_suffix, pad_to_cache_floor
from app.sandbox.docker_runner import run_in_sandbox
from app.workspace_map import build_workspace_map

FRONTEND_SCOPE_EXCLUDE = re.compile(r"^(playwright\.config\.ts|tests/)")

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
            "name": "write_file",
            "description": "Overwrite a file's contents in the worktree. Refused if the path is outside this category's write scope.",
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


class FixCouncilState(TypedDict, total=False):
    worktree_path: str
    touched_files: list[str]
    diff: str
    verifier_result: dict[str, Any]
    score: int
    rubric: list[dict]
    verdict: str
    attempt: int
    prior_rejection: str | None


def is_in_scope(relative_path: str) -> bool:
    return not FRONTEND_SCOPE_EXCLUDE.match(relative_path)


def retrieval_node(state: FixCouncilState) -> FixCouncilState:
    """One-hop static import scan — no vector search, no graph DB (plan.md §16).
    The fixture app is a single vanilla-JS file with no imports, so the touched
    file IS the whole neighborhood; this still runs the real scan rather than
    hardcoding that fact, so it generalizes to a repo that does import something.
    """
    worktree = Path(state["worktree_path"])
    app_js = worktree / "app.js"
    touched = ["app.js"]

    content = app_js.read_text()
    for match in re.finditer(r"""(?:import .* from ['"](.+?)['"]|require\(['"](.+?)['"]\))""", content):
        imported = match.group(1) or match.group(2)
        if imported and (worktree / imported).exists():
            touched.append(imported)

    for other in worktree.glob("*.js"):
        rel = other.name
        if rel == "app.js":
            continue
        other_content = other.read_text()
        if "app.js" in other_content or "app'" in other_content:
            touched.append(rel)

    return {**state, "touched_files": sorted(set(touched))}


def _tool_fingerprint(name: str, args: dict) -> str:
    canonical = json.dumps(args, sort_keys=True)
    return hashlib.sha256(f"{name}:{canonical}".encode()).hexdigest()


def patch_generation_node(state: FixCouncilState) -> FixCouncilState:
    worktree = Path(state["worktree_path"])
    workspace_map = build_workspace_map("amirzakaria-sf/whipguard-demo-ui", state["touched_files"])

    system_prompt = pad_to_cache_floor(
        build_prefix(
            role=(
                "You are a patch-generation worker fixing a UI bug. You have read_file, "
                "write_file, and finish_patch tools. Make the SMALLEST correct change. "
                "Call finish_patch only after you have actually run something to check your "
                "change (state what you ran in the summary)."
            ),
            workspace_map=workspace_map,
            category_rules=UI_CATEGORY_RULES,
        )
    )
    user_prompt = build_volatile_suffix(
        "Bug: deleting an item from the list removes the wrong item whenever more than "
        "one item exists (off-by-one in the delete handler in app.js). Fix it.",
        prior_attempt_rejection=state.get("prior_rejection"),
    )

    messages: list[dict] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    client = AzureOpenAI(
        azure_endpoint=settings.azure_api_endpoint,
        api_key=settings.azure_api_key,
        api_version=settings.azure_openai_api_version,
    )

    call_fingerprints: list[str] = []
    ran_a_check = False

    for _ in range(MAX_TOOL_ITERATIONS):
        response = client.chat.completions.create(
            model=settings.azure_worker_deployment,
            messages=messages,
            tools=TOOLS,
        )
        message = response.choices[0].message
        messages.append(message.model_dump(exclude_none=True))

        if not message.tool_calls:
            break

        for tool_call in message.tool_calls:
            name = tool_call.function.name
            try:
                args = json.loads(tool_call.function.arguments)
            except json.JSONDecodeError:
                args = {}

            fingerprint = _tool_fingerprint(name, args)
            call_fingerprints.append(fingerprint)
            repeat_streak = sum(1 for f in reversed(call_fingerprints[-REPEAT_FAIL_THRESHOLD:]) if f == fingerprint)

            if repeat_streak >= REPEAT_FAIL_THRESHOLD:
                messages.append(
                    {"role": "tool", "tool_call_id": tool_call.id, "content": "Attempt failed: repeated identical tool call too many times."}
                )
                return {**state, "diff": "", "verifier_result": {"error": "loop_tripwire_failed"}}

            if repeat_streak >= REPEAT_NUDGE_THRESHOLD:
                nudge = f" You have called {name} with identical arguments {repeat_streak} times — that will not produce a different result. Change your approach."
            else:
                nudge = ""

            if name == "read_file":
                target = worktree / args["path"]
                content = target.read_text() if target.exists() else f"ERROR: {args['path']} does not exist"
                result = content + nudge
            elif name == "write_file":
                rel_path = args["path"]
                if not is_in_scope(rel_path):
                    result = f"REFUSED: {rel_path} is outside the ui category's write scope." + nudge
                else:
                    (worktree / rel_path).write_text(args["content"])
                    result = f"wrote {rel_path}" + nudge
            elif name == "finish_patch":
                result = "acknowledged" + nudge
            else:
                result = f"unknown tool {name}"

            messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": result})

        if any(tc.function.name == "finish_patch" for tc in message.tool_calls):
            break

    # git diff reads metadata only — it does not execute the repo's own code, so
    # it runs on the host directly. A worktree's .git file points at an absolute
    # host path for its object store; that path doesn't exist inside the sandbox
    # container (only /work is mounted), which is why running this via
    # run_in_sandbox silently returned an empty diff instead of the real one.
    diff_proc = subprocess.run(
        ["git", "diff"], cwd=str(worktree), capture_output=True, text=True, timeout=30
    )

    return {**state, "diff": diff_proc.stdout}


def verifier_node(state: FixCouncilState) -> FixCouncilState:
    """Actually builds/runs the patched worktree and re-runs the real Playwright
    suite — reports what happened, never an opinion about what should happen."""
    worktree = state["worktree_path"]
    exit_code, stdout, stderr = run_in_sandbox(
        worktree, ["npm install --silent && npx playwright test"], timeout_seconds=180
    )
    return {
        **state,
        "verifier_result": {
            "passes": exit_code == 0,
            "output": stdout[-2000:],
        },
    }


def arbiter_node(state: FixCouncilState) -> FixCouncilState:
    if not state["verifier_result"].get("passes"):
        return {
            **state,
            "score": 0,
            "rubric": [{"factor": "verifier", "weight": 0, "note": "Playwright still fails after the patch."}],
            "verdict": "Patch does not resolve the original failing assertion.",
        }

    prefix = pad_to_cache_floor(
        build_prefix(
            role="You are the Arbiter scoring a proposed FIX. Score 0-100 given the diff and the Verifier's real test result.",
            workspace_map=build_workspace_map("amirzakaria-sf/whipguard-demo-ui", state["touched_files"]),
            category_rules=UI_CATEGORY_RULES,
        )
    )
    suffix = build_volatile_suffix(
        f"Diff:\n{state['diff']}\n\nVerifier: tests pass = {state['verifier_result']['passes']}"
    )
    verdict = azure_client.call_arbiter(prefix, suffix)
    return {
        **state,
        "score": verdict.score,
        "rubric": [f.model_dump() for f in verdict.factors],
        "verdict": verdict.verdict,
    }


def route_on_resolution_score(state: FixCouncilState) -> str:
    if state["score"] >= settings.resolution_threshold:
        return "propose"
    return "retry" if state.get("attempt", 1) < 2 else "hold"


def build_fix_council_graph():
    from langgraph.graph import END, StateGraph

    graph = StateGraph(FixCouncilState)
    graph.add_node("retrieval", retrieval_node)
    graph.add_node("patch_generation", patch_generation_node)
    graph.add_node("verifier", verifier_node)
    graph.add_node("arbiter", arbiter_node)

    graph.set_entry_point("retrieval")
    graph.add_edge("retrieval", "patch_generation")
    graph.add_edge("patch_generation", "verifier")
    graph.add_edge("verifier", "arbiter")
    graph.add_conditional_edges(
        "arbiter", route_on_resolution_score, {"propose": END, "retry": END, "hold": END}
    )
    return graph.compile()


def run_fix_council(worktree_path: str, attempt: int = 1, prior_rejection: str | None = None) -> FixCouncilState:
    graph = build_fix_council_graph()
    return graph.invoke(
        {"worktree_path": worktree_path, "attempt": attempt, "prior_rejection": prior_rejection}
    )
