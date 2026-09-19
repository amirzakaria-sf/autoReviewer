"""Global pause, in addition to the per-repo kill switches on Repo.

Per-repo pauses already exist. This is the one-click "stop everything" the
dashboard did not have: a runaway detector across every connected repo.
Stored in app_settings so both the web process and the worker see the same
value without a restart.
"""

from __future__ import annotations

from app import app_settings

DETECTION = "global_detection_paused"
PROPOSALS = "global_proposals_paused"


def detection_paused() -> bool:
    return app_settings.get_setting(DETECTION) == "true"


def proposals_paused() -> bool:
    return app_settings.get_setting(PROPOSALS) == "true"


def status() -> dict:
    return {
        "detection_paused": detection_paused(),
        "proposals_paused": proposals_paused(),
    }


def set_paused(*, detection: bool | None = None, proposals: bool | None = None) -> dict:
    if detection is not None:
        app_settings.set_setting(DETECTION, "true" if detection else "false")
    if proposals is not None:
        app_settings.set_setting(PROPOSALS, "true" if proposals else "false")
    return status()
