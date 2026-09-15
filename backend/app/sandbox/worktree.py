"""Per-repo bare mirror + per-fix worktrees (plan.md §4).

{repo}/mirror/            bare git mirror, fetched on webhook/poll, never worked in
{repo}/fixes/{n}-{slug}/  a git WORKTREE off mirror/, one per active fix
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from app.config import settings

WORKSPACE_ROOT = Path(settings.workspace_root)

# Matches docker_runner.py's own SANDBOX_UID/GID -- the sandbox container
# runs as this UID (the HOST user's, not root), but this backend process
# itself runs as root (no USER in the Dockerfile), so every worktree it
# creates is root-owned. A root-owned directory blocks the non-root sandbox
# from writing into it at all -- `npm install` inside failed completely
# silent (Docker returned an exit code with empty stdout/stderr, no
# "permission denied" visible anywhere) until this was tracked down by
# actually running the eval harness and comparing a raw `ls -la` against
# the sandbox's own `id`, not by assuming detection working once meant
# every worktree path was fine.
_SANDBOX_UID = int(os.environ.get("SANDBOX_UID", "1000"))
_SANDBOX_GID = int(os.environ.get("SANDBOX_GID", "1000"))


def _repo_slug(github_full_name: str) -> str:
    return github_full_name.replace("/", "__")


def mirror_path(github_full_name: str) -> Path:
    return WORKSPACE_ROOT / _repo_slug(github_full_name) / "mirror"


def ensure_mirror(github_full_name: str) -> Path:
    """Clone the bare mirror if it doesn't exist yet; fetch if it does."""
    path = mirror_path(github_full_name)
    path.parent.mkdir(parents=True, exist_ok=True)

    clone_url = f"https://{settings.github_token}@github.com/{github_full_name}.git"

    if not path.exists():
        subprocess.run(
            ["git", "clone", "--mirror", clone_url, str(path)],
            check=True,
            capture_output=True,
            text=True,
        )
    else:
        # Scoped to `main` only -- NOT a bare `fetch origin`, which (because
        # this is a --mirror clone, whose default refspec is +refs/*:refs/*)
        # tries to update every ref including branches WhipGuard itself pushed
        # back to origin earlier for an open PR. If one of those is currently
        # checked out in an active fix worktree, git refuses the whole fetch
        # outright ("refusing to fetch into branch ... checked out at ...") --
        # found by hitting it for real once a fix's branch existed on origin
        # and a second detection run tried to refresh the mirror.
        subprocess.run(
            ["git", "--git-dir", str(path), "fetch", "origin", "+refs/heads/main:refs/heads/main"],
            check=True,
            capture_output=True,
            text=True,
        )
    return path


def create_worktree(mirror: Path, issue_number: int, slug: str, base_branch: str = "main") -> Path:
    repo_root = mirror.parent
    worktree_path = repo_root / "fixes" / f"{issue_number}-{slug}"
    (repo_root / "fixes").mkdir(parents=True, exist_ok=True)

    branch_name = f"whipguard/{issue_number}-{slug}"
    # detect/recheck worktrees reuse a fixed name (issue_number=0) across every
    # run -- if a prior run's worktree dir was removed but the branch ref
    # survived (or removal simply hadn't run yet), `-b` below fails outright
    # because it refuses to create a branch that already exists. These are
    # always throwaway, so drop any stale ref before recreating it.
    subprocess.run(
        ["git", "--git-dir", str(mirror), "branch", "-D", branch_name],
        check=False,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [
            "git",
            "--git-dir",
            str(mirror),
            "worktree",
            "add",
            "-b",
            branch_name,
            str(worktree_path),
            base_branch,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    # Recursive, not just the top directory: `npm install` doesn't only
    # CREATE new entries (node_modules) -- it also REWRITES package-lock.json,
    # an EXISTING checked-out file. A checked-out file's default mode (git's
    # normal 644) denies write to anyone but its owner regardless of the
    # containing directory's own ownership, so chowning only the directory
    # (an earlier version of this fix) still left npm hitting a bare EACCES
    # on that one file -- found by dropping --silent, which had been
    # swallowing the real error behind an opaque, contentless exit 243.
    for root, dirs, files in os.walk(worktree_path):
        for name in dirs + files:
            os.chown(os.path.join(root, name), _SANDBOX_UID, _SANDBOX_GID)
    os.chown(worktree_path, _SANDBOX_UID, _SANDBOX_GID)
    return worktree_path


def remove_worktree(mirror: Path, worktree_path: Path) -> None:
    subprocess.run(
        ["git", "--git-dir", str(mirror), "worktree", "remove", "--force", str(worktree_path)],
        check=False,
        capture_output=True,
        text=True,
    )
    if worktree_path.exists():
        shutil.rmtree(worktree_path, ignore_errors=True)
