"""HTTP probe of a Cloudflare Pages preview URL.

The Playwright (or other category) re-check is the real gate. This only
answers "did the preview actually come up", which used to be hardcoded True.
"""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger("whipguard.preview")


def probe_preview_url(url: str, timeout_seconds: float = 10.0) -> bool:
    if not url:
        return False
    try:
        response = httpx.get(
            url,
            timeout=timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": "WhipGuard-preview-probe"},
        )
        return response.status_code < 500
    except Exception:
        logger.exception("preview probe failed for %s", url)
        return False
