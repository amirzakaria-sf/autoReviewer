"""Where a cloned repository lands on disk, and what happens when that moves.

Two things are being protected here. The first is tenancy: clones are grouped
under the organization that owns them, so "how much disk is this org using"
and "delete everything this org owns" are path operations rather than a
database query joined against a directory listing.

The second is the migration, which is the part that can actually lose work. A
repo that gains an organization changes directory, and a git worktree is held
together by two absolute paths pointing at each other -- so a move that
rewrites only one side leaves every open fix worktree unopenable while still
listed as present.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.sandbox import worktree as wt


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    root.mkdir()
    monkeypatch.setattr(wt, "WORKSPACE_ROOT", root)
    wt._org_cache.clear()
    yield root
    wt._org_cache.clear()


def _org(monkeypatch, slug: str) -> None:
    monkeypatch.setattr(wt, "_org_dir", lambda _full_name: slug)


def _git(*args: str, cwd: Path | None = None) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _repo_with_worktree(root: Path) -> tuple[Path, Path]:
    """A bare mirror plus one linked worktree, in the layout this module owns."""
    origin = root.parent / "origin"
    origin.mkdir()
    _git("init", "-q", "-b", "main", str(origin))
    _git("config", "user.email", "t@example.com", cwd=origin)
    _git("config", "user.name", "T", cwd=origin)
    (origin / "app.js").write_text("export const x = 1;\n")
    _git("add", "-A", cwd=origin)
    _git("commit", "-qm", "init", cwd=origin)

    repo_dir = root / "acme__demo"
    mirror = repo_dir / "mirror"
    mirror.parent.mkdir(parents=True)
    _git("clone", "--mirror", "-q", str(origin), str(mirror))
    _git("--git-dir", str(mirror), "worktree", "add", "--detach", "-q", str(repo_dir / "counsel"), "main")
    return repo_dir, mirror


# --- layout -------------------------------------------------------------------


def test_a_repo_lands_under_the_organization_that_owns_it(workspace, monkeypatch):
    _org(monkeypatch, "acme")
    assert wt.repo_root("acme/demo") == workspace / "acme" / "acme__demo"


def test_a_repo_with_no_organization_still_gets_a_deterministic_home(workspace, monkeypatch):
    _org(monkeypatch, wt._NO_ORG_DIR)
    assert wt.repo_root("acme/demo") == workspace / "_unassigned" / "acme__demo"


def test_the_mirror_path_is_derived_from_the_repo_root(workspace, monkeypatch):
    _org(monkeypatch, "acme")
    assert wt.mirror_path("acme/demo") == wt.repo_root("acme/demo") / "mirror"


def test_a_failed_org_lookup_is_not_cached(workspace, monkeypatch):
    """A database that is briefly down must not pin a repo to the fallback
    directory for the whole cache TTL -- that would clone it twice, in two
    places, for no reason anyone could see."""
    def _explode(*_args, **_kwargs):
        raise RuntimeError("database is down")

    monkeypatch.setattr("app.sync_db.connection", _explode)
    assert wt._org_dir("acme/demo") == wt._NO_ORG_DIR
    assert "acme/demo" not in wt._org_cache


# --- migration ----------------------------------------------------------------


def test_a_flat_legacy_tree_is_moved_rather_than_re_cloned(workspace, monkeypatch):
    """Re-cloning would be simpler and would silently abandon every fix
    worktree that was mid-flight when the repo joined an organization."""
    legacy = workspace / "acme__demo"
    (legacy / "mirror").mkdir(parents=True)
    (legacy / "fixes" / "7-fix-thing").mkdir(parents=True)
    marker = legacy / "fixes" / "7-fix-thing" / "patch.txt"
    marker.write_text("work in progress")

    _org(monkeypatch, "acme")
    resolved = wt.repo_root("acme/demo")

    assert resolved == workspace / "acme" / "acme__demo"
    assert (resolved / "fixes" / "7-fix-thing" / "patch.txt").read_text() == "work in progress"
    assert not legacy.exists()


def test_a_failed_org_lookup_never_drags_the_tree_into_the_fallback(workspace, monkeypatch):
    """`_org_dir` answers `_unassigned` for "no org" AND for "the lookup
    failed", and cannot tell them apart by design -- a database hiccup must
    not crash a council run.

    Relocating on that answer is what turned a survivable miss into a
    destructive one in production: one failed lookup moved the mirror, the
    Counsel checkout and every open fix worktree into `_unassigned/`, and the
    next call -- database healthy again -- moved all of it back. The tree stays
    where it is until something can say for certain where it belongs.
    """
    owned = workspace / "acme" / "acme__demo"
    (owned / "mirror").mkdir(parents=True)
    (owned / "fixes" / "21-issue-21").mkdir(parents=True)
    (owned / "fixes" / "21-issue-21" / "patch.txt").write_text("mid-flight")

    def _explode(*_args, **_kwargs):
        raise RuntimeError("database is down")

    monkeypatch.setattr("app.sync_db.connection", _explode)
    resolved = wt.repo_root("acme/demo")

    assert resolved == owned, "a failed lookup must resolve to the tree that exists"
    assert (owned / "fixes" / "21-issue-21" / "patch.txt").read_text() == "mid-flight"
    assert not (workspace / "_unassigned").exists(), "nothing may be moved on a failed lookup"


def test_a_repo_that_genuinely_has_no_org_still_gets_the_fallback_for_a_fresh_clone(workspace, monkeypatch):
    """The fallback stays a destination for a NEW clone -- it just stops being
    a relocation target for a tree that already exists elsewhere."""
    _org(monkeypatch, wt._NO_ORG_DIR)
    assert wt.repo_root("acme/demo") == workspace / "_unassigned" / "acme__demo"


def test_a_repo_moving_between_organizations_takes_its_tree_with_it(workspace, monkeypatch):
    old = workspace / "old-org" / "acme__demo"
    (old / "mirror").mkdir(parents=True)
    (old / "keep.txt").write_text("still here")

    _org(monkeypatch, "new-org")
    resolved = wt.repo_root("acme/demo")

    assert resolved == workspace / "new-org" / "acme__demo"
    assert (resolved / "keep.txt").read_text() == "still here"
    assert not old.exists()


def test_both_halves_of_a_worktree_link_are_rewritten_by_the_move(workspace, monkeypatch):
    """The bug this catches is quiet: `git worktree list` keeps reporting the
    worktree as present while every git command inside it fails with "not a
    git repository", because only the mirror's half of the link was fixed."""
    repo_dir, _ = _repo_with_worktree(workspace)

    _org(monkeypatch, "acme")
    resolved = wt.repo_root("acme/demo")
    counsel = resolved / "counsel"

    assert counsel.is_dir()
    head = subprocess.run(
        ["git", "-C", str(counsel), "rev-parse", "HEAD"],
        capture_output=True, text=True,
    )
    assert head.returncode == 0, head.stderr
    assert str(resolved) in (resolved / "mirror" / "worktrees" / "counsel" / "gitdir").read_text()


def test_a_link_recorded_under_a_path_that_never_existed_here_is_still_repaired(workspace):
    """Worktrees created under a different WORKSPACE_ROOT (a venv run writing
    host paths a container never had) record a mirror path that does not
    resolve at all, which `git worktree repair` cannot recover from. The
    layout is this module's own invariant, so it does not need to."""
    repo_dir, mirror = _repo_with_worktree(workspace)
    (repo_dir / "counsel" / ".git").write_text("gitdir: /nowhere/mirror/worktrees/counsel\n")
    (mirror / "worktrees" / "counsel" / "gitdir").write_text("/nowhere/counsel/.git\n")

    wt._repair_worktree_links(mirror, repo_dir)

    head = subprocess.run(
        ["git", "-C", str(repo_dir / "counsel"), "rev-parse", "HEAD"],
        capture_output=True, text=True,
    )
    assert head.returncode == 0, head.stderr


def test_a_read_only_workspace_reports_the_legacy_path_instead_of_moving_it(workspace, monkeypatch):
    """The web process mounts the workspace read-only by design (plan.md §15).
    It must still find an un-migrated tree -- returning the not-yet-existing
    new path would make every read fail until the worker happened to touch
    that repo."""
    legacy = workspace / "acme__demo"
    (legacy / "mirror").mkdir(parents=True)

    _org(monkeypatch, "acme")
    monkeypatch.setattr(wt.os, "access", lambda *_args, **_kwargs: False)

    assert wt.repo_root("acme/demo") == legacy
    assert legacy.exists()


# --- worktree creation ---------------------------------------------------------


def test_creating_a_worktree_over_an_existing_one_succeeds(workspace, monkeypatch):
    """The path is reused, not unique per run: detect/recheck worktrees use a
    fixed name every run, and a REVISION regenerates the patch for the same
    issue under the same branch. `git worktree add` refuses a path that
    already exists, so the second call died with a bare exit 255 and the
    reworked fix never appeared -- found by asking for a revision against the
    real deployment."""
    repo_dir, mirror = _repo_with_worktree(workspace)

    first = wt.create_worktree(mirror, 13, "issue-13")
    (first / "scratch.txt").write_text("left over from the first attempt")

    second = wt.create_worktree(mirror, 13, "issue-13")

    assert second == first
    assert second.is_dir()
    assert not (second / "scratch.txt").exists(), "the rework must start from a clean checkout"
    head = subprocess.run(
        ["git", "-C", str(second), "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True,
    )
    assert head.stdout.strip() == "whipguard/13-issue-13"


def test_the_authenticated_remote_names_a_username_git_can_use():
    """`https://<token>@github.com/...` makes git read the token as a USERNAME
    with no password and then try to prompt for one. Cloning a public repo
    never needs the credential, so it succeeded and the mistake stayed
    invisible until the first PUSH failed with "could not read Password" --
    which named no URL at all. Found by approving a real fix."""
    from app.config import settings

    remote = wt.authenticated_remote("acme/demo")

    assert remote.startswith("https://x-access-token:")
    assert remote.endswith("@github.com/acme/demo.git")
    assert f"https://{settings.github_token}@" not in remote


def test_git_never_blocks_on_a_credential_prompt():
    """A worker with no terminal that prompts for a password hangs forever
    holding its queue item. This turns that into a clean, attributable error."""
    assert wt._git_env()["GIT_TERMINAL_PROMPT"] == "0"
