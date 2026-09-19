from __future__ import annotations

import re
from pathlib import Path

from app.categories import DetectionResult
from app.graph_index import _extract_symbols, _INDEXABLE_SUFFIXES as _SYMBOL_SUFFIXES

# plan.md §2's "doc-vs-code drift check (does README/API doc match real route
# signatures)" -- the concrete, mechanical reading built here: every
# function-call-shaped reference in README.md's inline code spans
# (`someFunction(...)`) must name a symbol that actually exists in the
# source. A real symbol table already exists (app/graph_index.py) for the
# call-graph; this reuses its own extraction rather than a second parser.
_BACKTICK_CALL = re.compile(r"`(\w+)\(")

_SKIP_DIRS = {"node_modules", ".git", "test-results", "playwright-report"}


class DocumentationDetector:
    def run(self, worktree_path: str, path_scope: str = "", base_url: str = "") -> DetectionResult:
        # base_url is accepted for interface parity and ignored: this check
        # reads source, so there is nothing at a deployed url for it to visit.
        # Saying so beats silently accepting a url and doing nothing with it.
        _ = base_url
        root = Path(worktree_path)
        # A scoped run walks one subtree. Resolved against the worktree so
        # a caller cannot point it outside.
        scoped_root = root
        if path_scope:
            candidate = (root / path_scope).resolve()
            if str(candidate).startswith(str(root.resolve())) and candidate.exists():
                scoped_root = candidate
        readme = root / "README.md"
        if not readme.exists():
            return DetectionResult(failed=False, assertion_text="No README.md to check.")

        real_symbols: set[str] = set()
        for path in scoped_root.rglob("*"):
            if not path.is_file() or path.suffix not in _SYMBOL_SUFFIXES:
                continue
            if any(part in _SKIP_DIRS for part in path.parts):
                continue
            try:
                content = path.read_text(errors="ignore")
            except Exception:
                continue
            for symbol in _extract_symbols(content):
                real_symbols.add(symbol["name"])

        readme_text = readme.read_text(errors="ignore")
        referenced = set(_BACKTICK_CALL.findall(readme_text))
        drifted = sorted(referenced - real_symbols)

        if not drifted:
            return DetectionResult(
                failed=False,
                assertion_text=f"All {len(referenced)} function reference(s) in README.md match real symbols.",
            )

        return DetectionResult(
            failed=True,
            assertion_text=(
                f"README.md references function(s) that don't exist in the source: {', '.join(drifted)}. "
                f"Real symbols found: {', '.join(sorted(real_symbols)) or '(none)'}."
            ),
        )
