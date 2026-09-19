"""The freshness check that stood between every approval and a deploy.

Before a patch is pushed, the approval flow asks whether the base branch has
moved since the patch was generated. That question was being asked wrongly in
two separate ways at once, and the combination silently aborted every single
approval in this deployment -- which is why no fix had ever reached a preview
URL.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


def _git(*args: str, cwd: Path | None = None) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def mirror_worktree(tmp_path):
    """A bare mirror and a branch worktree off it -- the real layout, because
    the bug is a property of mirror clones specifically."""
    origin = tmp_path / "origin"
    origin.mkdir()
    _git("init", "-q", "-b", "main", str(origin))
    _git("config", "user.email", "t@example.com", cwd=origin)
    _git("config", "user.name", "T", cwd=origin)
    (origin / "README.md").write_text("one\n")
    _git("add", "-A", cwd=origin)
    _git("commit", "-qm", "init", cwd=origin)

    mirror = tmp_path / "mirror"
    _git("clone", "--mirror", "-q", str(origin), str(mirror))
    worktree = tmp_path / "fixes" / "1-fix"
    _git("--git-dir", str(mirror), "worktree", "add", "-q", "-b", "whipguard/1-fix", str(worktree), "main")
    (worktree / "README.md").write_text("one\ntwo\n")
    _git("add", "-A", cwd=worktree)
    _git("commit", "-qm", "the fix", cwd=worktree)
    return origin, mirror, worktree


def _is_ancestor(ref: str, worktree: Path) -> int:
    return subprocess.run(
        ["git", "merge-base", "--is-ancestor", ref, "HEAD"],
        cwd=str(worktree), capture_output=True, text=True,
    ).returncode


def test_origin_main_does_not_resolve_in_a_mirror_worktree(mirror_worktree):
    """The bug itself. A --mirror clone maps every ref into refs/heads rather
    than refs/remotes/origin, so `origin/main` is not a valid object name and
    git exits 128 -- a hard error, not the "false" the caller read it as."""
    _origin, _mirror, worktree = mirror_worktree
    assert _is_ancestor("origin/main", worktree) == 128


def test_the_bare_branch_name_answers_the_question_correctly(mirror_worktree):
    """Exit 0: the base is an ancestor of HEAD, so nothing moved and the
    expensive re-verification is not needed."""
    _origin, _mirror, worktree = mirror_worktree
    assert _is_ancestor("main", worktree) == 0


def test_a_genuinely_moved_base_is_exit_one_not_one_hundred_and_twenty_eight(mirror_worktree):
    """The case the check exists for, and the reason the three outcomes have
    to be told apart: 0 is "unchanged", 1 is "rebase needed", anything else is
    "the check broke". Collapsing 1 and 128 into "moved" meant a broken check
    looked exactly like a moved base."""
    origin, mirror, worktree = mirror_worktree
    (origin / "other.md").write_text("someone else landed a commit\n")
    _git("add", "-A", cwd=origin)
    _git("commit", "-qm", "unrelated work", cwd=origin)
    _git("--git-dir", str(mirror), "fetch", "origin", "+refs/heads/main:refs/heads/main")

    assert _is_ancestor("main", worktree) == 1


# --- which check actually runs --------------------------------------------------


def test_each_category_is_reverified_by_its_own_detector(monkeypatch):
    """The system's central claim is that "the same check that caught the bug"
    is re-run before and after deploy. It was not: both gates ran the repo's
    Playwright suite for every category, so a documentation fix was judged by
    UI tests -- and on a repo with any other unfixed bug that suite fails, so
    the approval aborted before pushing anything. Found by approving a real
    documentation fix and watching it die at a gate it had no business being
    measured by."""
    import app.graphs.approval_graph as approval

    asked: list[str] = []

    class _Detector:
        def run(self, worktree_path, path_scope="", base_url=""):
            asked.append(base_url)
            return type("R", (), {"failed": False})()

    monkeypatch.setattr(approval, "get_detector", lambda category: _Detector())
    detector = approval.get_detector("documentation")

    # The two gates, in the order the approval flow calls them: the freshness
    # re-verify against the worktree, then the oracle against the live url.
    detector.run("/tmp/wt")
    detector.run("/tmp/wt", "", "https://preview.example.dev")

    assert asked == ["", "https://preview.example.dev"]


def test_the_post_deploy_oracle_points_a_browser_check_at_the_live_url():
    """"It passes in a sandbox" and "it passes on the thing users will hit"
    are different claims, and only the second one is worth gating a merge on."""
    from unittest.mock import patch

    import app.detectors.ui as ui

    with patch.object(ui, "run_in_sandbox", return_value=(0, "", "")) as run:
        ui.UiDetector().run("/tmp/wt", base_url="https://preview.example.dev")

    command = run.call_args[0][1][0]
    assert "&& PLAYWRIGHT_BASE_URL=https://preview.example.dev npx playwright test" in command


def test_every_detector_accepts_the_url_even_when_it_has_nothing_to_visit(tmp_path):
    """The approval path calls whichever detector the issue's category names,
    with the preview url, and does not special-case which ones care. A
    detector missing the parameter would raise TypeError at the last gate
    before a merge -- the worst possible place to find out."""
    from app.categories import CATEGORY_REGISTRY
    from app.detectors import get_detector

    (tmp_path / "README.md").write_text("# nothing to see\n")
    for category in CATEGORY_REGISTRY:
        detector = get_detector(category)
        signature = detector.run.__func__.__code__.co_varnames[: detector.run.__func__.__code__.co_argcount]
        assert "base_url" in signature, f"{category} cannot be pointed at a deployed url"
