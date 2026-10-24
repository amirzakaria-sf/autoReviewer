"""Approving or rejecting an access request, in one place.

Called by the in-app admin endpoints (routers/admin.py) and by the operator console contract
(routers/console_api.py), so the same state change and the same email happen whichever door the reviewer used.
Both functions commit; neither raises for a mail problem (a decision must not be undone by SMTP).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import AccessRequestStatus
from app.integrations import email_client
from app.models import AccessRequest
from app.security import sign_invite_token

logger = logging.getLogger("whipguard.access_decisions")


async def approve(db: AsyncSession, row: AccessRequest, *, decided_by: uuid.UUID | None, org_id: str = "") -> AccessRequest:
    if row.status != AccessRequestStatus.PENDING:
        raise HTTPException(409, f"already {row.status.value}")

    org_id = (org_id or "").strip()
    if org_id:
        from app.models import Organization

        try:
            organization = await db.get(Organization, uuid.UUID(org_id))
        except ValueError as error:
            raise HTTPException(400, "that is not a valid organization id") from error
        if organization is None:
            raise HTTPException(404, "that organization does not exist")
        row.org_id = organization.id

    row.status = AccessRequestStatus.APPROVED
    row.decided_by = decided_by
    row.decided_at = datetime.now(timezone.utc)
    await db.commit()

    if email_client.smtp_configured():
        try:
            subject, html, text_body = email_client.build_invite_email(row.name, sign_invite_token(row.id))
            await asyncio.to_thread(email_client.send_email, row.email, subject, html, text_body)
        except Exception:
            logger.exception("failed to send invite email for access request %s", row.id)
    return row


async def reject(db: AsyncSession, row: AccessRequest, *, decided_by: uuid.UUID | None, reason: str = "") -> AccessRequest:
    if row.status != AccessRequestStatus.PENDING:
        raise HTTPException(409, f"already {row.status.value}")

    reason = (reason or "").strip()
    row.status = AccessRequestStatus.REJECTED
    row.decided_by = decided_by
    row.decided_at = datetime.now(timezone.utc)
    row.decision_reason = reason or None
    await db.commit()

    if email_client.smtp_configured():
        try:
            subject, html, text_body = email_client.build_access_declined_email(row.name, reason)
            await asyncio.to_thread(email_client.send_email, row.email, subject, html, text_body)
        except Exception:
            logger.exception("failed to send decline email for access request %s", row.id)
    return row
