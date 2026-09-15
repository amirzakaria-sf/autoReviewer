from __future__ import annotations

from app.categories import DetectionResult
from app.sandbox.docker_runner import run_in_sandbox


class AccessibilityDetector:
    """Runs the repo's dedicated axe-core check (tests/accessibility.spec.ts,
    tagged @a11y) -- scoped to that one file so it never overlaps with the
    `ui` category's own Playwright run, which excludes @a11y via
    --grep-invert (see UiDetector). Proven live against a real WCAG
    color-contrast violation in the fixture app before this was wired in."""

    def run(self, worktree_path: str) -> DetectionResult:
        exit_code, stdout, stderr = run_in_sandbox(
            worktree_path,
            ["npm install --silent && npx playwright test tests/accessibility.spec.ts"],
            timeout_seconds=180,
        )
        return DetectionResult(
            failed=exit_code != 0,
            assertion_text=stdout[-2000:],
            stderr=stderr[-1000:],
        )
