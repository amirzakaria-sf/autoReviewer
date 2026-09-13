"""Per-repo bare mirror + per-fix worktrees (plan.md §4).

{repo}/mirror/            bare git mirror, fetched on webhook/poll, never worked in
{repo}/fixes/{n}-{slug}/  a git WORKTREE off mirror/, one per active fix
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from app.config import settings

WORKSPACE_ROOT = Path(__file__).resolve().parents[3] / "workspace"


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
        subprocess.run(
            ["git", "--git-dir", str(path), "fetch", "--prune", "origin"],
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
