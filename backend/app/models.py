import uuid
from datetime import datetime

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.embeddings import EMBEDDING_DIMENSIONS
from app.enums import FixStatus, IssueStatus


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class Repo(Base):
    __tablename__ = "repos"

    id: Mapped[uuid.UUID] = _uuid_pk()
    github_full_name: Mapped[str] = mapped_column(sa.String, nullable=False, unique=True)
    default_branch: Mapped[str] = mapped_column(sa.String, default="main")
    # {category_key: {"issues": bool, "fixes": bool}} -- the toggle matrix
    # (plan.md §8) rendered straight from CATEGORY_REGISTRY keys; absent here
    # means "on" (see enabled_categories_for in app/categories.py).
    enabled_categories: Mapped[dict] = mapped_column(JSONB, default=dict)
    # {category_key: {"assurance": int, "resolution": int}} overriding the
    # registry's own defaults, per-repo (plan.md §7.1's threshold sliders).
    thresholds: Mapped[dict] = mapped_column(JSONB, default=dict)
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
    # Bumped by the ORM on every UPDATE (any column, not just status) --
    # what the stuck-run sweeper (app/stuck_run_sweeper.py) reads to tell a
    # genuinely-abandoned IN_PROGRESS row from one still mid-flow.
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now()
    )


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


class CalibrationEvent(Base):
    """plan.md §5.1/§5.4: a real, mechanically-observed outcome for a fix
    AFTER it shipped -- merged, reopened, rejected, verification/outcome
    failed, or swept as stuck. app/calibration.py's threshold-tuning job
    reads these to nudge Repo.thresholds in code; never fed back into a
    prompt (that would be the model grading its own homework)."""

    __tablename__ = "calibration_events"

    id: Mapped[uuid.UUID] = _uuid_pk()
    fix_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), sa.ForeignKey("fixes.id"))
    outcome: Mapped[str] = mapped_column(sa.String)
    detail: Mapped[dict] = mapped_column(JSONB, default=dict)
    observed_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.func.now())


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


class CodeChunk(Base):
    """One embedding space of plan.md §5.2's hybrid retrieval: function/
    component-level chunks, re-embedded on push. RetrievalNode's vector
    similarity search reads this table; the one-hop static import scan stays
    as-is alongside it (plan.md §13.1's own two-signal design, not an
    either/or)."""

    __tablename__ = "code_chunks"
    __table_args__ = (sa.UniqueConstraint("repo_id", "file_path", "symbol_name", name="uq_code_chunk_identity"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    repo_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), sa.ForeignKey("repos.id"))
    file_path: Mapped[str] = mapped_column(sa.String)
    symbol_name: Mapped[str] = mapped_column(sa.String)
    content: Mapped[str] = mapped_column(sa.Text)
    embedding: Mapped[list] = mapped_column(Vector(EMBEDDING_DIMENSIONS))
    updated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now())


class IssueEmbedding(Base):
    """Second embedding space: past issue+fix text, for dedupe ("have we
    raised this before") and (once Phase 9's CalibrationEvent exists) whether
    that past fix actually held."""

    __tablename__ = "issue_embeddings"

    id: Mapped[uuid.UUID] = _uuid_pk()
    issue_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), sa.ForeignKey("issues.id"), unique=True)
    text: Mapped[str] = mapped_column(sa.Text)
    embedding: Mapped[list] = mapped_column(Vector(EMBEDDING_DIMENSIONS))
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.func.now())


class HumanInputRequest(Base):
    """The generalized clarifying-question primitive (plan.md §10.5) --
    WaitForApprovalNode is the degenerate instance of this (one `confirm`
    question, two options), not a separate system. Fires from three places:
    the Arbiter's own needs_clarification field, the Fix Council ReAct loop's
    ask_human tool, and (not yet wired) a human replying to an approval
    request with a question instead of a yes/no.

    Implemented as application-level state (a DB row + a resume endpoint),
    not LangGraph's own checkpointer/interrupt primitive specifically -- the
    graphs here run via a single blocking invoke() rather than a
    checkpoint-and-resume execution model, so "suspend the graph" is honestly
    "the graph returns early, and a separate call continues the workflow
    from where the DB row says it stopped." The user-visible mechanism (a
    real structured question, a real pause, a real resume with the answer
    folded into context) is the same either way."""

    __tablename__ = "human_input_requests"

    id: Mapped[uuid.UUID] = _uuid_pk()
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    issue_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), sa.ForeignKey("issues.id"), nullable=True)
    fix_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), sa.ForeignKey("fixes.id"), nullable=True)
    node_name: Mapped[str] = mapped_column(sa.String)
    kind: Mapped[str] = mapped_column(sa.String)  # confirm | single_select | multi_select | free_text
    question: Mapped[str] = mapped_column(sa.Text)
    options: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # [{id, label, detail}]
    allow_other: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    context: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(sa.String, default="pending")  # pending | answered | expired
    answer: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    answered_by: Mapped[str | None] = mapped_column(sa.String, nullable=True)
    answered_via: Mapped[str | None] = mapped_column(sa.String, nullable=True)
    thread: Mapped[list] = mapped_column(JSONB, default=list)  # [{from: "agent"|"human", text, at}]
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.func.now())
    answered_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)


class Symbol(Base):
    """The dependency graph, modeled as edges in Postgres (plan.md §5.3) --
    NOT a dedicated graph database first. The actual question the Fix Council
    needs answered is narrow ("what depends on this symbol, so I know what I
    might break?"), and that's one indexed query away here. Named Tier-2
    upgrade path if that ever stops being true: move Symbol/Edge into
    Neo4j/Memgraph behind the same query interface -- not taken now because
    the narrow question doesn't need it yet."""

    __tablename__ = "symbols"
    __table_args__ = (sa.UniqueConstraint("repo_id", "path", "name", name="uq_symbol_identity"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    repo_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), sa.ForeignKey("repos.id"))
    path: Mapped[str] = mapped_column(sa.String)
    name: Mapped[str] = mapped_column(sa.String)
    kind: Mapped[str] = mapped_column(sa.String)  # function | const | class
    line_start: Mapped[int] = mapped_column(sa.Integer)
    line_end: Mapped[int] = mapped_column(sa.Integer)
    updated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now())


class Edge(Base):
    __tablename__ = "edges"
    __table_args__ = (sa.UniqueConstraint("from_symbol_id", "to_symbol_id", "kind", name="uq_edge_identity"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    from_symbol_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), sa.ForeignKey("symbols.id"))
    to_symbol_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), sa.ForeignKey("symbols.id"))
    kind: Mapped[str] = mapped_column(sa.String)  # calls | imports | extends | references
