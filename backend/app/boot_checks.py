"""Process-start invariants that must fail loudly, not after the first request."""

from __future__ import annotations

import logging

from app.config import settings

logger = logging.getLogger("whipguard.boot")

_DEMO_PASSWORD = "whipguard-demo"
_DEMO_SESSION_SECRET = "change-me-in-real-deployments"


def secrets_are_insecure() -> bool:
    return (
        settings.admin_password == _DEMO_PASSWORD
        or settings.session_secret == _DEMO_SESSION_SECRET
    )


def refuse_insecure_defaults() -> None:
    """Refuse to boot a reachable process on the fixture credentials.

    Tests set ALLOW_INSECURE_DEFAULTS=true in conftest.py before Settings is
    constructed. A docker-compose deployment that still has the demo password
    is the failure this exists to catch.
    """
    if not secrets_are_insecure():
        return
    if settings.allow_insecure_defaults:
        logger.warning(
            "booting with demo ADMIN_PASSWORD / SESSION_SECRET because "
            "ALLOW_INSECURE_DEFAULTS is set — do not do this on a public host"
        )
        return
    raise RuntimeError(
        "ADMIN_PASSWORD and SESSION_SECRET still have their demo defaults. "
        "Set them in .env, or set ALLOW_INSECURE_DEFAULTS=true only for tests "
        "and a private local venv."
    )
