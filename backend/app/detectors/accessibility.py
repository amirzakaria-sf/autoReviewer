from __future__ import annotations

from app.categories import DetectionResult
from app.sandbox.docker_runner import run_in_sandbox


class AccessibilityDetector:
    """Runs the repo's dedicated axe-core check (tests/accessibility.spec.ts,
    tagged @a11y) -- scoped to that one file so it never overlaps with the
    `ui` category's own Playwright run, which excludes @a11y via
    --grep-invert (see UiDetector). Proven live against a real WCAG
    color-contrast violation in the fixture app before this was wired in."""

    def run(self, worktree_path: str, path_scope: str = "", base_url: str = "") -> DetectionResult:
        # path_scope is accepted for interface parity and ignored: this suite
        # is addressed by test name, not by source path, so narrowing it to a
        # directory would silently run everything while claiming otherwise.
        _ = path_scope
        # Prefixed onto the command rather than passed as a sandbox env var:
        # run_in_sandbox takes a shell command, and Playwright reads this
        # variable itself. Empty means "the repo's own default", which is the
        # local dev server the config starts.
        prefix = f"PLAYWRIGHT_BASE_URL={base_url} " if base_url else ""
        # pnpm + shared store (docker_runner.py) -- see UiDetector for why.
        # Not `--silent`: see UiDetector for the exact opaque-exit-243 bug
        # a quiet install caused here before.
        exit_code, stdout, stderr = run_in_sandbox(
            worktree_path,
            [
                'npx --yes pnpm@9 install --store-dir=/pnpm-store --reporter=append-only '
                f"&& {prefix}npx playwright test tests/accessibility.spec.ts"
            ],
            timeout_seconds=180,
        )
        return DetectionResult(
            failed=exit_code != 0,
            assertion_text=stdout[-2000:],
            stderr=stderr[-1000:],
        )
