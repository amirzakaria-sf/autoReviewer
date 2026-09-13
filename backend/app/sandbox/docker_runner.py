"""Throwaway Docker sandbox for running a fix's build/test/Playwright commands.

All repo code executes inside this container, never on the orchestrator's own host
(plan.md §13) — a connected repository is untrusted input the moment it's
connected. The volume mount IS the write-scope boundary: the container can only
ever see the one worktree directory it was given.
"""

from __future__ import annotations

import os
import subprocess

SANDBOX_IMAGE = "mcr.microsoft.com/playwright:v1.63.0-jammy"


def run_in_sandbox(
    worktree_path: str, command: list[str], timeout_seconds: int = 300
) -> tuple[int, str, str]:
    # Run as the host's own UID/GID, not root. A container running as root writes
    # files (npm installs, browser caches) the host user then can't clean up on
    # worktree removal — found by actually running this once and inspecting the
    # result, exactly the "zero errors is not proof it works" lesson (plan.md
    # §9.8): `rm -rf` on the worktree silently left root-owned files behind.
    docker_command = [
        "docker",
        "run",
        "--rm",
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "-e",
        "HOME=/tmp",
        "-v",
        f"{worktree_path}:/work",
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
