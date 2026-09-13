"""Repo workspace map, computed by CODE, not discovered by tool calls (plan.md §9.4).

Putting the answer in the prefix instead of ordering a model to glob for it is the
single highest-leverage token optimization in this design — it runs once, here,
before the first model call of an attempt.
"""

from __future__ import annotations


def build_workspace_map(repo_slug: str, touched_files: list[str] | None = None) -> str:
    lines = [
        f"repo: {repo_slug}",
        "stack: static HTML/CSS/vanilla JS (no build step)",
        "entry point: index.html",
        "frontend scope glob: everything except playwright.config.ts and tests/**",
    ]
    if touched_files:
        lines.append("files touched by this issue: " + ", ".join(touched_files))
    else:
        lines.append(
            "no live preview exists yet for this attempt; deploy/verification tools "
            "will fail until one does — this is expected at this stage, not an error"
        )
    return "\n".join(lines)
