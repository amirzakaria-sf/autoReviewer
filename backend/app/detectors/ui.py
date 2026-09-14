from __future__ import annotations

from app.categories import DetectionResult
from app.sandbox.docker_runner import run_in_sandbox


class UiDetector:
    def run(self, worktree_path: str) -> DetectionResult:
        exit_code, stdout, stderr = run_in_sandbox(
            worktree_path, ["npm install --silent && npx playwright test"], timeout_seconds=180
        )
        return DetectionResult(
            failed=exit_code != 0,
            assertion_text=stdout[-2000:],
            stderr=stderr[-1000:],
        )
