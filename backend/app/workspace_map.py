"""Repo workspace map, computed by CODE, not discovered by tool calls (plan.md §9.4).

Putting the answer in the prefix instead of ordering a model to glob for it is the
single highest-leverage token optimization in this design — it runs once, here,
before the first model call of an attempt.
"""

from __future__ import annotations

from pathlib import Path


def build_workspace_map(
    repo_slug: str, touched_files: list[str] | None = None, worktree_path: str = ""
) -> str:
    lines = [f"repo: {repo_slug}"]
    root = _resolve_root(repo_slug, worktree_path)
    if root is not None:
        lines.extend(_describe_layout(root))
    else:
        lines.append("stack: not yet inspected (no worktree mounted for this call)")
        lines.append("entry point: unknown")

    if touched_files:
        lines.append("files touched by this issue: " + ", ".join(touched_files))
    else:
        lines.append(
            "no live preview exists yet for this attempt; deploy/verification tools "
            "will fail until one does — this is expected at this stage, not an error"
        )
    return "\n".join(lines)


def _resolve_root(repo_slug: str, worktree_path: str) -> Path | None:
    if worktree_path:
        candidate = Path(worktree_path)
        if candidate.is_dir():
            return candidate
    if not repo_slug or "/" not in repo_slug:
        return None
    try:
        from app.sandbox.worktree import repo_root

        counsel = repo_root(repo_slug) / "counsel"
        if counsel.is_dir():
            return counsel
    except Exception:
        return None
    return None


def _describe_layout(root: Path) -> list[str]:
    markers: list[str] = []
    if (root / "package.json").exists():
        markers.append("node (package.json)")
    if (root / "pyproject.toml").exists() or (root / "pytest.ini").exists():
        markers.append("python")
    if (root / "go.mod").exists():
        markers.append("go")
    if (root / "Cargo.toml").exists():
        markers.append("rust")
    if (root / "playwright.config.ts").exists() or (root / "playwright.config.js").exists():
        markers.append("playwright")
    if (root / "index.html").exists() and (root / "app.js").exists() and not (root / "package.json").exists():
        markers.append("static HTML/CSS/JS")

    entry = (
        "index.html"
        if (root / "index.html").exists()
        else "package.json"
        if (root / "package.json").exists()
        else "pyproject.toml"
        if (root / "pyproject.toml").exists()
        else "(none detected)"
    )
    frontend_hint = (
        "frontend: markup/styles/client JS under the repo root or src/; "
        "do not rewrite tests to make a detector pass"
    )
    return [
        "stack: " + (", ".join(markers) if markers else "unknown (inspect the worktree)"),
        f"entry point: {entry}",
        frontend_hint,
    ]
