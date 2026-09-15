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
        # `npx --yes pnpm@9 install --store-dir=/pnpm-store`, not `npm
        # install`: a shared content-addressable store (docker_runner.py)
        # means a package already resolved by ANY prior worktree, for ANY
        # repo, is never re-downloaded -- proven live (26s cold, ~2s warm).
        # Not `--silent`/quiet by default on purpose: an install failure
        # needs to stay visible in assertion_text/stderr (see ui.py's git
        # history for the exact opaque-exit-243 bug that swallowing real npm
        # errors caused here before).
        exit_code, stdout, stderr = run_in_sandbox(
            worktree_path,
            [
                'npx --yes pnpm@9 install --store-dir=/pnpm-store --reporter=append-only '
                '&& npx playwright test --grep-invert "@a11y"'
            ],
            timeout_seconds=180,
        )
        return DetectionResult(
            failed=exit_code != 0,
            assertion_text=stdout[-2000:],
            stderr=stderr[-1000:],
        )
