from __future__ import annotations

from pathlib import Path

from app.categories import DetectionResult

# plan.md §2's "bundle-size diff" -- the pragmatic reading actually built here
# is a budget check (total shipped JS/CSS under a size ceiling), not a diff
# against a stored prior-commit baseline (that needs a baseline-storage
# mechanism this build doesn't have yet). Named as the real scope, not hidden
# behind the plan's own phrase. A real Lighthouse run is the natural upgrade
# once there's a live preview to point it at (verifier_node already deploys
# one for ui/security; performance doesn't reuse that path yet).
BUNDLE_BUDGET_BYTES = 5 * 1024  # 5KB -- deliberately tight for a fixture-sized app

_INDEXABLE_SUFFIXES = (".js",)
_SKIP_DIRS = {"node_modules", ".git", "tests", "test-results", "playwright-report", "backend"}


class PerformanceDetector:
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
        total_bytes = 0
        files: list[tuple[str, int]] = []

        for path in scoped_root.rglob("*"):
            if not path.is_file() or path.suffix not in _INDEXABLE_SUFFIXES:
                continue
            if any(part in _SKIP_DIRS for part in path.parts):
                continue
            size = path.stat().st_size
            total_bytes += size
            files.append((str(path.relative_to(root)), size))

        breakdown = "\n".join(f"{name}: {size}B" for name, size in files)

        if total_bytes <= BUNDLE_BUDGET_BYTES:
            return DetectionResult(
                failed=False,
                assertion_text=f"Total shipped JS: {total_bytes}B, within budget ({BUNDLE_BUDGET_BYTES}B).\n{breakdown}",
            )

        return DetectionResult(
            failed=True,
            assertion_text=(
                f"Total shipped JS is {total_bytes}B, over the {BUNDLE_BUDGET_BYTES}B budget "
                f"by {total_bytes - BUNDLE_BUDGET_BYTES}B.\n{breakdown}"
            ),
        )
