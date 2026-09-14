import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.enums import FixStatus, IssueStatus


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class Repo(Base):
    __tablename__ = "repos"

    id: Mapped[uuid.UUID] = _uuid_pk()
    github_full_name: Mapped[str] = mapped_column(sa.String, nullable=False, unique=True)
    default_branch: Mapped[str] = mapped_column(sa.String, default="main")
    connected_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.func.now())


class Issue(Base):
    __tablename__ = "issues"

    id: Mapped[uuid.UUID] = _uuid_pk()
    repo_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), sa.ForeignKey("repos.id"))
    category: Mapped[str] = mapped_column(sa.String, default="ui")
    origin: Mapped[str] = mapped_column(
        sa.String, sa.CheckConstraint("origin in ('detected','filed-externally')"), default="detected"
    )
    github_issue_number: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    title: Mapped[str] = mapped_column(sa.String)
    severity: Mapped[int] = mapped_column(sa.Integer, default=1)
    assurance_score: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    assurance_rubric: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    evidence: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[IssueStatus] = mapped_column(
        sa.Enum(IssueStatus, name="issue_status"), default=IssueStatus.DETECTED_BELOW_THRESHOLD
    )
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.func.now())


class Fix(Base):
    __tablename__ = "fixes"

    id: Mapped[uuid.UUID] = _uuid_pk()
    issue_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), sa.ForeignKey("issues.id"))
    patch_hash: Mapped[str | None] = mapped_column(sa.String, nullable=True)
    resolution_score: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    resolution_rubric: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    branch_name: Mapped[str | None] = mapped_column(sa.String, nullable=True)
    pr_number: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    preview_url: Mapped[str | None] = mapped_column(sa.String, nullable=True)
    slack_message_ts: Mapped[str | None] = mapped_column(sa.String, nullable=True)
    status: Mapped[FixStatus] = mapped_column(sa.Enum(FixStatus, name="fix_status"), default=FixStatus.AWAITING_APPROVAL)
    approved_by: Mapped[str | None] = mapped_column(sa.String, nullable=True)
    approved_via: Mapped[str | None] = mapped_column(sa.String, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    deployed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.func.now())


class CouncilRun(Base):
    __tablename__ = "council_runs"

    id: Mapped[uuid.UUID] = _uuid_pk()
    issue_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), sa.ForeignKey("issues.id"), nullable=True)
    fix_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), sa.ForeignKey("fixes.id"), nullable=True)
    role: Mapped[str] = mapped_column(sa.String)
    model: Mapped[str] = mapped_column(sa.String)
    prompt_hash: Mapped[str] = mapped_column(sa.String)
    input_tokens: Mapped[int] = mapped_column(sa.Integer, default=0)
    cached_input_tokens: Mapped[int] = mapped_column(sa.Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(sa.Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(sa.Integer, default=0)
    verdict: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.func.now())


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (sa.UniqueConstraint("fix_id", "issue_id", "condition_key", name="uq_notification_condition"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    fix_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), sa.ForeignKey("fixes.id"), nullable=True)
    issue_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), sa.ForeignKey("issues.id"), nullable=True)
    condition_key: Mapped[str] = mapped_column(sa.String)
    occurrence_count: Mapped[int] = mapped_column(sa.Integer, default=1)
    first_seen_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.func.now())
    last_seen_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.func.now())
    notified_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    notify_count: Mapped[int] = mapped_column(sa.Integer, default=0)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = _uuid_pk()
    actor_user_id: Mapped[str | None] = mapped_column(sa.String, nullable=True)
    actor_surface: Mapped[str] = mapped_column(sa.String)
    action: Mapped[str] = mapped_column(sa.String)
    target_type: Mapped[str] = mapped_column(sa.String)
    target_id: Mapped[str] = mapped_column(sa.String)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.func.now())


class OutcomeCheck(Base):
    __tablename__ = "outcome_checks"

    id: Mapped[uuid.UUID] = _uuid_pk()
    fix_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), sa.ForeignKey("fixes.id"))
    github_state: Mapped[dict] = mapped_column(JSONB)
    cloudflare_state: Mapped[dict] = mapped_column(JSONB)
    slack_state: Mapped[dict] = mapped_column(JSONB)
    dashboard_state: Mapped[str] = mapped_column(sa.String)
    agreed: Mapped[bool] = mapped_column(sa.Boolean)
    mismatch_detail: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    checked_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.func.now())
