"""Throwaway Docker sandbox for running a fix's build/test/Playwright commands.

All repo code executes inside this container, never on the orchestrator's own host
(plan.md §13) — a connected repository is untrusted input the moment it's
connected. The volume mount IS the write-scope boundary: the container can only
ever see the one worktree directory it was given.
"""

from __future__ import annotations

import os
import subprocess

from app.config import settings

SANDBOX_IMAGE = "mcr.microsoft.com/playwright:v1.63.0-jammy"

# The UID/GID that owns the bind-mounted workspace directory ON THE HOST. Not
# os.getuid() — when this orchestrator itself runs inside a container (as root,
# to reach the host's Docker socket easily), os.getuid() would return 0 and
# reintroduce the exact "sandbox writes root-owned files nothing can clean up"
# bug this was written to fix (found by actually running it once, per plan.md
# §9.8). Override via SANDBOX_UID/SANDBOX_GID if the host user isn't 1000:1000.
SANDBOX_UID = os.environ.get("SANDBOX_UID", "1000")
SANDBOX_GID = os.environ.get("SANDBOX_GID", "1000")


def _host_path(worktree_path: str) -> str:
    """Translate a path as seen inside the orchestrator's own filesystem into
    the equivalent path on the HOST — required because a bind-mount source
    passed to `docker run` is resolved by the HOST's Docker daemon, not by this
    process. When not containerized (WORKSPACE_HOST_PATH unset), the two are
    the same path."""
    if not settings.workspace_host_path:
        return worktree_path

    from app.sandbox.worktree import WORKSPACE_ROOT

    container_root = str(WORKSPACE_ROOT)
    if worktree_path.startswith(container_root):
        return worktree_path.replace(container_root, settings.workspace_host_path, 1)
    return worktree_path


# A pnpm content-addressable store, shared across every sandbox invocation for
# every category/fix/repo -- every detector that installs JS deps (ui.py,
# accessibility.py) uses `pnpm install --store-dir=/pnpm-store` instead of
# `npm install`, so a package already resolved once (by ANY worktree, for ANY
# repo) is never re-downloaded again. Verified live: a cold install took 26s;
# a second worktree reusing this same store took ~2s, "reused 88, downloaded
# 0". Filesystem-level hardlink deduplication of node_modules itself did NOT
# work in this environment despite the store and worktrees sharing a device
# (found by actually comparing inodes after forcing
# --package-import-method=hardlink, not by assuming pnpm's docs applied
# unmodified here) -- each worktree's node_modules is still a real,
# undeduplicated copy on disk. The network/CPU savings are real and kept;
# the disk-bloat problem this was ALSO meant to solve still has to be solved
# by prompt worktree cleanup (webhooks.py deletes a fix's worktree once its
# PR merges), not by this store.
_PNPM_STORE_HOST_PATH = os.path.join(settings.workspace_host_path or settings.workspace_root, ".pnpm-store")
PNPM_STORE_CONTAINER_PATH = "/pnpm-store"


def run_in_sandbox(
    worktree_path: str, command: list[str], timeout_seconds: int = 300
) -> tuple[int, str, str]:
    mount_source = _host_path(worktree_path)
    # Created by THIS process, which runs as root in the containerized
    # deployment -- same class of bug as worktree.py's own chown (a
    # root-created directory blocks the non-root sandbox from writing into
    # it at all), so this needs the identical fix.
    store_dir = os.path.join(settings.workspace_root, ".pnpm-store")
    if not os.path.isdir(store_dir):
        os.makedirs(store_dir, exist_ok=True)
        os.chown(store_dir, int(SANDBOX_UID), int(SANDBOX_GID))

    docker_command = [
        "docker",
        "run",
        "--rm",
        "--user",
        f"{SANDBOX_UID}:{SANDBOX_GID}",
        "-e",
        "HOME=/tmp",
        "-v",
        f"{mount_source}:/work",
        "-v",
        f"{_PNPM_STORE_HOST_PATH}:{PNPM_STORE_CONTAINER_PATH}",
        "-w",
        "/work",
        SANDBOX_IMAGE,
        "bash",
        "-c",
        " ".join(command),
    ]

    result = subprocess.run(
        docker_command,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
    )
    return result.returncode, result.stdout, result.stderr
