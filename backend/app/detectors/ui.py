from __future__ import annotations

from app.categories import DetectionResult
from app.sandbox.docker_runner import run_in_sandbox


class UiDetector:
    def run(self, worktree_path: str) -> DetectionResult:
        # --grep-invert excludes tests/accessibility.spec.ts (tagged @a11y in
        # its title) -- that file belongs to the `accessibility` category's
        # own detector; without this exclusion the same failure would be
        # raised twice, once under each category.
        #
        # `npm install --no-audit --no-fund --loglevel=error`, NOT
        # `--silent`: --silent suppresses real errors too, not just routine
        # noise -- an EACCES writing package-lock.json (the worktree's
        # checked-out files are root-owned, since the backend process that
        # creates worktrees runs as root; create_worktree chowns them to the
        # sandbox's own UID specifically so this can write) came back as an
        # opaque, contentless exit 243 under --silent, and only appeared as
        # readable text once that flag was dropped.
        exit_code, stdout, stderr = run_in_sandbox(
            worktree_path,
            ['npm install --no-audit --no-fund --loglevel=error && npx playwright test --grep-invert "@a11y"'],
            timeout_seconds=180,
        )
        return DetectionResult(
            failed=exit_code != 0,
            assertion_text=stdout[-2000:],
            stderr=stderr[-1000:],
        )
