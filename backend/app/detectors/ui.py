from __future__ import annotations

from app.categories import DetectionResult
from app.sandbox.docker_runner import run_in_sandbox


class UiDetector:
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
                f'&& {prefix}npx playwright test --grep-invert "@a11y"'
            ],
            timeout_seconds=180,
        )
        return DetectionResult(
            failed=exit_code != 0,
            assertion_text=stdout[-2000:],
            stderr=stderr[-1000:],
        )
