"""Counsel's tool surface.

Every tool here is a thin wrapper over machinery the platform already
maintains -- hybrid retrieval, the call graph, the subsystem map, negative
traces, the issue record. That is deliberate and it is the whole moat: a
general chatbot can describe code it was shown, but it cannot answer "what
did we already try on this bug and why was it rejected", because it has no
corpus of prior attempts to read.

Three rules hold for every tool in this file:

**Almost everything is read-only.** The exceptions are the two dispatch tools
at the bottom (`draft_prd`, `investigate`), and they do not execute anything
either -- they add a row to the work queue for the privileged worker to pick
up, which is how every other privileged action in this system already works.
Counsel cannot choose the command, the image, the mount or the credentials.
There is deliberately no tool that approves, rejects, deploys or merges: an
agent that reads attacker-controlled repository content must not hold the
capability a malicious file would ask it to use.

**Results are data, never instructions.** Tool output routinely contains
repository content, which is attacker-controlled in the general case. A file
that says "ignore previous instructions" is a string this function returned,
nothing more.

**Failure returns a sentence, not an exception.** A tool that raises kills
the turn; a tool that explains why it found nothing lets the model recover
and say so honestly.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("whipguard.counsel.tools")

_GIT_TIMEOUT = 20


@dataclass
class ToolContext:
    """What the caller is allowed to see, resolved once per conversation.

    Carrying identity here rather than in the prompt is the difference
    between a permission and a suggestion.
    """

    repo_id: str
    repo_full_name: str
    worktree_path: str
    user_email: str
    is_admin: bool


# --- code understanding ------------------------------------------------------


def search_code(context: ToolContext, query: str) -> str:
    """Fused retrieval across the structural, lexical, dense and graph channels."""
    from app.hybrid_retrieval import hybrid_retrieve, render

    chunks = hybrid_retrieve(
        query, repo_id=context.repo_id, worktree_path=context.worktree_path, limit=5
    )
    return render(context.repo_full_name, query, chunks)


def explain_subsystem(context: ToolContext, topic: str) -> str:
    """Which files implement a named capability, derived rather than declared."""
    from app.repo_map import find_subsystem, render_subsystem

    subsystem = find_subsystem(topic, repo_id=context.repo_id, worktree_path=context.worktree_path)
    if not subsystem.files:
        return render_subsystem(context.repo_full_name, subsystem)

    # The file list alone is an index, not an explanation. Pulling the entry
    # points' actual source in means the model reasons over real code instead
    # of inferring behaviour from filenames.
    rendered = [render_subsystem(context.repo_full_name, subsystem), "", "SOURCE OF THE ENTRY POINTS:"]
    for path in subsystem.entry_points[:3]:
        rendered.append(read_file(context, path, 1, 120))
    return "\n\n".join(rendered)


def repo_structure(context: ToolContext) -> str:
    """The project skeleton, ranked by how much depends on each file."""
    from app.repo_map import build_repo_map, render_repo_map

    return render_repo_map(context.repo_full_name, build_repo_map(context.repo_id))


def locate_symbol(context: ToolContext, name: str) -> str:
    from app.graph_index import find_symbols

    hits = find_symbols(context.repo_id, name, limit=6)
    if not hits:
        return f"No symbol named '{name}' is indexed in {context.repo_full_name}."
    return "\n".join(f"- {hit['kind']} {hit['name']} -- {hit['path']}:{hit['line']}" for hit in hits)


def blast_radius(context: ToolContext, symbol: str) -> str:
    """What breaks if this symbol changes."""
    from app.graph_index import symbol_blast_radius

    rows = symbol_blast_radius(context.repo_id, symbol, max_depth=2)
    if not rows:
        return f"Nothing in the indexed call graph reaches '{symbol}'. It may be an entry point, or unused."
    ordered = sorted(rows, key=lambda row: (row["depth"], row["path"]))[:15]
    lines = [f"Changing `{symbol}` is reachable from:"]
    lines += [f"  - {row['name']} ({row['path']}:{row['line']}) at {row['depth']} hop(s)" for row in ordered]
    return "\n".join(lines)


def read_file(context: ToolContext, path: str, start_line: int = 1, end_line: int = 200) -> str:
    from app.lexical_index import read_file_slice

    text = read_file_slice(
        Path(context.worktree_path), path, start_line=int(start_line), end_line=int(end_line)
    )
    if not text:
        return f"Could not read {path}:{start_line}-{end_line} — the file may not exist at that path."
    return f"### {path}:{start_line}-{end_line}\n```\n{text}\n```"


# --- attribution -------------------------------------------------------------


def _git(context: ToolContext, *args: str) -> str:
    from app.sandbox.worktree import mirror_path

    result = subprocess.run(
        ["git", "-C", str(mirror_path(context.repo_full_name)), *args],
        capture_output=True, text=True, timeout=_GIT_TIMEOUT,
    )
    return result.stdout if result.returncode == 0 else ""


def who_wrote(context: ToolContext, path: str, start_line: int = 1, end_line: int = 60) -> str:
    """Three different questions hide behind "who wrote this", and only the
    last is usually what the asker means.

    `blame` answers who last TOUCHED a line, which is frequently a formatter
    or a rename. `log -L` answers who introduced it. Neither answers "who
    should I ask", which is about sustained involvement -- so all three are
    reported, labelled, rather than picking one and calling it the author.
    """
    blame = _git(
        context, "blame", "-w", "-M", "-C", "--line-porcelain",
        f"-L{int(start_line)},{int(end_line)}", "HEAD", "--", path,
    )
    if not blame:
        return f"No git history for {path}:{start_line}-{end_line} — the path may be wrong or untracked."

    last_touched: dict[str, int] = {}
    for line in blame.splitlines():
        if line.startswith("author "):
            name = line[len("author "):].strip()
            last_touched[name] = last_touched.get(name, 0) + 1

    history = _git(context, "log", "--format=%an|%ar", f"-L{int(start_line)},{int(end_line)}:{path}", "--no-patch")
    introduced = ""
    entries = [entry for entry in history.splitlines() if "|" in entry]
    if entries:
        author, when = entries[-1].split("|", 1)
        introduced = f"{author.strip()} ({when.strip()})"

    ownership = knowledge_owners(context, path)

    parts = [f"ATTRIBUTION FOR {path}:{start_line}-{end_line}", ""]
    parts.append("Last touched these lines:")
    for name, count in sorted(last_touched.items(), key=lambda item: -item[1]):
        parts.append(f"  - {name} — {count} line(s)")
    if introduced:
        parts += ["", f"Introduced by: {introduced}"]
    parts += ["", ownership]
    parts += [
        "",
        "Note: blame reports who last edited a line, which is often a formatter, a rename or a "
        "merge rather than whoever wrote the logic.",
    ]
    return "\n".join(parts)


def knowledge_owners(context: ToolContext, path: str) -> str:
    """Who has sustained involvement in this file — the question people
    actually mean by "who knows this area". Commit count plus recency, not a
    single blame line."""
    log = _git(context, "log", "--format=%an|%ar", "--", path)
    if not log:
        return f"No commit history for {path}."

    counts: dict[str, int] = {}
    latest: dict[str, str] = {}
    for entry in log.splitlines():
        if "|" not in entry:
            continue
        author, when = entry.split("|", 1)
        author = author.strip()
        counts[author] = counts.get(author, 0) + 1
        latest.setdefault(author, when.strip())

    ranked = sorted(counts.items(), key=lambda item: -item[1])[:5]
    lines = [f"Who has worked on {path} (by sustained involvement):"]
    lines += [f"  - {name} — {count} commit(s), most recently {latest[name]}" for name, count in ranked]
    return "\n".join(lines)


# --- WhipGuard's own record --------------------------------------------------


def prior_attempts(context: ToolContext, query: str) -> str:
    """What this repo already tried and how it failed."""
    from app.memory_traces import render_history, search_history

    rows = search_history(context.repo_id, query)
    return render_history(query, rows)


def list_issues(context: ToolContext, status: str = "") -> str:
    import psycopg

    from app import sync_db

    clause, params = "", [context.repo_id]
    if status:
        clause = "AND status = %s"
        params.append(status)
    with sync_db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT id, category, title, severity, assurance_score, status, created_at
            FROM issues WHERE repo_id = %s {clause}
            ORDER BY created_at DESC LIMIT 25
            """,
            params,
        )
        rows = cur.fetchall()
    if not rows:
        return "No issues match." if status else "No issues recorded for this repo yet."
    lines = [f"ISSUES IN {context.repo_full_name}:"]
    for row in rows:
        lines.append(
            f"  - [{str(row[0])[:8]}] {row[2]}\n"
            f"      {row[1]} · severity {row[3]} · score {row[4]} · {row[5]} · {row[6]:%Y-%m-%d}"
        )
    return "\n".join(lines)


def issue_detail(context: ToolContext, issue_id: str) -> str:
    """Everything recorded about one finding, including the rubric that
    explains its score — the "why was this an 82" question."""
    import json

    import psycopg

    from app.retrieval import _sync_dsn

    with sync_db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, category, title, severity, assurance_score, assurance_rubric,
                   evidence, status, created_at
            FROM issues WHERE repo_id = %s AND id::text LIKE %s LIMIT 1
            """,
            (context.repo_id, f"{issue_id}%"),
        )
        row = cur.fetchone()
    if not row:
        return f"No issue in this repo matches id '{issue_id}'."

    rubric = row[5] or {}
    evidence = row[6] or {}
    parts = [
        f"ISSUE {str(row[0])[:8]} — {row[2]}",
        f"{row[1]} · severity {row[3]} · assurance confidence {row[4]} · {row[7]}",
        "",
        f"Verdict: {rubric.get('verdict', 'none recorded')}",
    ]
    for factor in rubric.get("factors", []):
        parts.append(f"  - {factor.get('factor')} (+{factor.get('weight')}): {factor.get('note')}")
    assertion = str(evidence.get("assertion_text", ""))[:1500]
    if assertion:
        parts += ["", "Mechanical evidence:", "```", assertion, "```"]
    return "\n".join(parts)


# --- dispatch ----------------------------------------------------------------
#
# These are the ONLY tools that cause anything to happen, and they cause it
# the same way every other privileged action in this system does: by adding a
# row to the work queue for the worker to pick up. Counsel cannot choose the
# command, the image, the mount or the credentials -- only ask for work that
# was already possible through the UI.


def research_web(context: ToolContext, question: str) -> str:
    """Look something up on the live web, then show only what survived curation.

    Deliberately not a raw search box. What comes back is the Curator's kept
    findings with their source URLs (app/research.py), so an answer built on
    this cites a page that was actually returned rather than a plausible URL.
    Read-only in the sense that matters here: it changes nothing about the
    repository, the issues or any fix -- the audit rows it writes are the
    record of having looked.
    """
    from app.research import research

    outcome = research(
        question,
        purpose=f"answering a question about {context.repo_full_name}",
        repo_id=context.repo_id,
        role="counsel_research",
    )
    return outcome.render()


def _enqueue_job(context: ToolContext, kind: str, payload: dict) -> str:
    """Queue a long job and hand back its id so the UI can follow it."""
    import uuid as uuid_module

    from app.work_queue import enqueue_sync

    job_id = str(uuid_module.uuid4())
    body = {
        **payload,
        "job_id": job_id,
        "repo_id": context.repo_id,
        "repo_full_name": context.repo_full_name,
        "user_email": context.user_email,
        "is_admin": context.is_admin,
    }
    enqueue_sync(kind, body)
    return job_id


def draft_prd(context: ToolContext, requirements: str) -> str:
    job_id = _enqueue_job(context, "counsel_prd", {"requirements": requirements})
    return (
        f"__JOB__{job_id}__PRD\n"
        "Started a feasibility council on those requirements. It reads the codebase, classifies "
        "each requirement against it, argues with its own draft, then scores how much of the "
        "result is actually grounded. Takes a few minutes — progress appears in the panel."
    )


def investigate(context: ToolContext, hypothesis: str, categories: str = "") -> str:
    wanted = [part.strip() for part in (categories or "").split(",") if part.strip()]
    job_id = _enqueue_job(
        context, "counsel_investigate", {"hypothesis": hypothesis, "categories": wanted}
    )
    dispatch = f" and asked the Bug Council to run: {', '.join(wanted)}" if wanted else ""
    return (
        f"__JOB__{job_id}__INVESTIGATION\n"
        f"Started an investigation into that{dispatch}. I gather the evidence; whether it is a real "
        "defect is the Bug Council's ruling, not mine."
    )


# --- registry ----------------------------------------------------------------

READ_TOOLS = {
    "search_code": search_code,
    "explain_subsystem": explain_subsystem,
    "repo_structure": repo_structure,
    "locate_symbol": locate_symbol,
    "blast_radius": blast_radius,
    "read_file": read_file,
    "who_wrote": who_wrote,
    "knowledge_owners": knowledge_owners,
    "prior_attempts": prior_attempts,
    "list_issues": list_issues,
    "issue_detail": issue_detail,
    "research_web": research_web,
}

# Kept separate from READ_TOOLS so "does this surface mutate anything" stays a
# question with a one-line answer.
ACTION_TOOLS = {
    "draft_prd": draft_prd,
    "investigate": investigate,
}

# Shown to the user as the tool runs, so the sidebar says "Mapping the
# subsystem" rather than "explain_subsystem".
TOOL_LABELS = {
    "search_code": "Searching the codebase",
    "explain_subsystem": "Mapping the subsystem",
    "repo_structure": "Reading the project structure",
    "locate_symbol": "Locating the symbol",
    "blast_radius": "Tracing what depends on it",
    "read_file": "Reading source",
    "who_wrote": "Checking git attribution",
    "knowledge_owners": "Working out who knows this area",
    "prior_attempts": "Searching what was already tried",
    "list_issues": "Listing issues",
    "issue_detail": "Reading the issue record",
    "research_web": "Researching this on the web",
    "draft_prd": "Convening the feasibility council",
    "investigate": "Opening an investigation",
}

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "search_code",
            "description": (
                "Search this repository's source for code relevant to a question. Fuses exact-identifier, "
                "keyword, semantic and call-graph signals. Use this first for any question about how "
                "something works."
            ),
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "What to look for, in natural language or as an identifier."}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "explain_subsystem",
            "description": (
                "Find every file that implements a named capability (e.g. 'authentication', 'the "
                "communications layer') and return its entry points with real source. Use for "
                "'explain X' and 'how does X work' questions."
            ),
            "parameters": {
                "type": "object",
                "properties": {"topic": {"type": "string", "description": "The capability to map, e.g. 'notifications'."}},
                "required": ["topic"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "repo_structure",
            "description": "The project's most-depended-on files. Use to orient before a broad question.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "locate_symbol",
            "description": "Find where a named function, class or constant is defined.",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "blast_radius",
            "description": "What calls a symbol, transitively. Use to answer 'what breaks if I change this'.",
            "parameters": {
                "type": "object",
                "properties": {"symbol": {"type": "string"}},
                "required": ["symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a line range of a file. Use after locating something, to quote it exactly.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Repo-relative path."},
                    "start_line": {"type": "integer"},
                    "end_line": {"type": "integer"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "who_wrote",
            "description": (
                "Git attribution for a line range: who last touched it, who introduced it, and who has "
                "sustained involvement. Use for 'who wrote this' questions."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "start_line": {"type": "integer"},
                    "end_line": {"type": "integer"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "knowledge_owners",
            "description": "Who has worked on a file most, by commit count and recency. Use for 'who should I ask about X'.",
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
            "name": "prior_attempts",
            "description": (
                "What WhipGuard already tried on this repo and how it failed — rejected fixes, failed "
                "verifications, findings that scored below threshold. Use before suggesting an approach."
            ),
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_issues",
            "description": "Issues WhipGuard has recorded for this repo.",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "description": "Optional filter: raised, fix-proposed, detected-below-threshold, closed.",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "issue_detail",
            "description": "Full record of one issue including the rubric behind its score. Use for 'why was this scored X'.",
            "parameters": {
                "type": "object",
                "properties": {"issue_id": {"type": "string", "description": "Full or short (8-char) issue id."}},
                "required": ["issue_id"],
            },
        },
    },
]


def run_tool(context: ToolContext, name: str, arguments: dict) -> str:
    handler = READ_TOOLS.get(name) or ACTION_TOOLS.get(name)
    if handler is None:
        return f"No such tool: {name}."
    try:
        return handler(context, **arguments)
    except TypeError as error:
        return f"Tool {name} rejected those arguments: {error}"
    except Exception as error:  # noqa: BLE001 - a failed tool must not end the turn
        logger.warning("tool %s failed: %s", name, error)
        return f"Tool {name} failed: {type(error).__name__}: {error}"


TOOL_SCHEMAS.append(
    {
        "type": "function",
        "function": {
            "name": "research_web",
            "description": (
                "Look up something on the live web that this repository cannot answer: a "
                "third-party API's current contract, whether a method was deprecated, what a "
                "service requires, what changed in a recent release. Results are curated before "
                "you see them -- every claim carries the source URL it was attributed to, and "
                "anything that could not be attributed was dropped. Cite those URLs. Do not use "
                "this for questions about this repository's own code; the code tools answer "
                "those better and for free."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "One specific, searchable technical question.",
                    }
                },
                "required": ["question"],
            },
        },
    }
)

TOOL_SCHEMAS += [
    {
        "type": "function",
        "function": {
            "name": "draft_prd",
            "description": (
                "Convene the feasibility council to write a technical PRD grounded in this codebase: "
                "it classifies each requirement as already-possible, extend-existing, build-new or "
                "conflicts, with citations. Use when the user describes features they want to add and "
                "wants scope or feasibility. Takes minutes and runs in the background."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "requirements": {
                        "type": "string",
                        "description": "The features wanted, verbatim from the user where possible.",
                    },
                },
                "required": ["requirements"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "investigate",
            "description": (
                "Open an investigation into a suspected problem: gathers the relevant code and prior "
                "attempts, and optionally asks the Bug Council to run detectors. Use for user reports "
                "like 'checkout feels slow' where evidence is needed before anyone can rule on it."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "hypothesis": {"type": "string", "description": "What is suspected, in plain words."},
                    "categories": {
                        "type": "string",
                        "description": (
                            "Optional comma-separated detector categories to run: ui, backend, "
                            "security, performance, accessibility, documentation."
                        ),
                    },
                },
                "required": ["hypothesis"],
            },
        },
    },
]
