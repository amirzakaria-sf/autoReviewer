from __future__ import annotations

from app.categories import DetectionResult
from app.sandbox.docker_runner import run_in_sandbox


class BackendDetector:
    """Runs the repo's own backend test suite. Node's built-in test runner
    (`node --test`) is used for the fixture repo -- no extra dependency to
    install, and it's the same "the repo's own suite, not a re-implementation
    of one" principle the UI detector applies to Playwright."""

    def run(self, worktree_path: str, path_scope: str = "", base_url: str = "") -> DetectionResult:
        # base_url is accepted for interface parity and ignored: this check
        # reads source, so there is nothing at a deployed url for it to visit.
        # Saying so beats silently accepting a url and doing nothing with it.
        _ = base_url
        # path_scope is accepted for interface parity and ignored: this suite
        # is addressed by test name, not by source path, so narrowing it to a
        # directory would silently run everything while claiming otherwise.
        _ = path_scope
        # A bare directory argument to `node --test <dir>` raised a spurious
        # MODULE_NOT_FOUND on this sandbox image's Node build (v24) -- found
        # by actually running it once and reading the real error, rather than
        # assuming a passing "some node --test invocation" proved the
        # detector worked (plan.md §9.8's exact lesson). An explicit glob of
        # the test files themselves is what real Node test-runner discovery
        # actually wants here.
        exit_code, stdout, stderr = run_in_sandbox(
            worktree_path, ["node --test backend/*.test.js"], timeout_seconds=60
        )
        return DetectionResult(
            failed=exit_code != 0,
            assertion_text=stdout[-2000:],
            stderr=stderr[-1000:],
        )
