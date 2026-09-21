import uuid
from datetime import datetime

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.embeddings import EMBEDDING_DIMENSIONS
from app.enums import (
    AccessRequestStatus,
    FixReviewStatus,
    FixStatus,
    IssueStatus,
    OrgRole,
    Seniority,
    UserRole,
    UserStatus,
)


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _uuid_pk()
    email: Mapped[str] = mapped_column(sa.String, nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(sa.String, nullable=False)
    role: Mapped[UserRole] = mapped_column(sa.Enum(UserRole, name="user_role"), default=UserRole.MEMBER)
    status: Mapped[UserStatus] = mapped_column(sa.Enum(UserStatus, name="user_status"), default=UserStatus.ACTIVE)
    first_name: Mapped[str | None] = mapped_column(sa.String, nullable=True)
    last_name: Mapped[str | None] = mapped_column(sa.String, nullable=True)
    mobile_number: Mapped[str | None] = mapped_column(sa.String, nullable=True)
    # Set the moment the user either connects something or explicitly skips
    # the first-login onboarding modal (routers/api.py's /me/onboarding) --
    # null is exactly "never decided yet", which is what gates the modal,
    # not a separate boolean that could drift out of sync with this.
    onboarding_completed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.func.now())
    last_login_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True)


class AccessRequest(Base):
    """A request for access, collected BEFORE any account exists (name,
    email, reason -- the thing this app was missing: the old flow let
    someone pick a password at signup time and created a real, if inactive,
    User row immediately). An admin reviews name/email/reason and decides;
    only on approval does anyone get a way to actually set a password, via a
    signed, single-use, expiring invite link mailed to the request's own
    email address (routers/auth.py's /complete-invite). Rejecting, or never
    deciding, leaves no account behind at all.
    """

    __tablename__ = "access_requests"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(sa.String, nullable=False)
    email: Mapped[str] = mapped_column(sa.String, nullable=False)
    reason: Mapped[str] = mapped_column(sa.Text, nullable=False)
    status: Mapped[AccessRequestStatus] = mapped_column(
        sa.Enum(AccessRequestStatus, name="access_request_status"), default=AccessRequestStatus.PENDING
    )
    decided_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    decision_reason: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    # Which organization the approving admin put them in. Nullable for the
    # very first account on a deployment, where no organization exists yet.
    #
    # Without this the approval path dead-ended: it produced an active account
    # belonging to no organization, which sees no repositories, no issues and
    # a page saying so. Half the onboarding flow led there.
    org_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=True
    )
    # Single-use guard for the invite link -- checked/set atomically inside
    # the same transaction that creates the User row, not just relied on
    # implicitly (an approved request being reused after the account already
    # exists must fail loudly, not silently create a second account attempt).
    invite_consumed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.func.now())


class RefreshToken(Base):
    """A stateful, revocable refresh token -- deliberately NOT a bare long-
    lived JWT, since a pure-stateless refresh token can't be revoked without
    a separate blocklist anyway. token_hash is a SHA-256 hash of the raw
    secret (the raw value only ever lives in the httpOnly cookie); rotated
    on every use (routers/auth.py's /refresh) -- a token presented twice is
    proof of theft or a replay, so the second use revokes the whole chain.
    """

    __tablename__ = "refresh_tokens"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), sa.ForeignKey("users.id"))
    token_hash: Mapped[str] = mapped_column(sa.String, nullable=False, unique=True)
    # The token this one was rotated FROM, so a reused (already-rotated) token
    # can be traced back to revoke every descendant in the same chain -- the
    # actual theft-detection mechanism, not just single-token expiry.
    replaced_by_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.func.now())
    user_agent: Mapped[str | None] = mapped_column(sa.String, nullable=True)


class Repo(Base):
    __tablename__ = "repos"

    id: Mapped[uuid.UUID] = _uuid_pk()
    github_full_name: Mapped[str] = mapped_column(sa.String, nullable=False, unique=True)
    default_branch: Mapped[str] = mapped_column(sa.String, default="main")
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True)
    # The tenant boundary, and the ONLY column in the schema that carries it
    # (see Organization's docstring). Declared here rather than added to
    # production by hand, which is how it existed until a fresh database
    # exposed the difference: `column "org_id" of relation "repos" does not
    # exist`.
    org_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=True, index=True
    )
    # {category_key: {"issues": bool, "fixes": bool}} -- the toggle matrix
    # (plan.md §8) rendered straight from CATEGORY_REGISTRY keys; absent here
    # means "on" (see enabled_categories_for in app/categories.py).
    # server_default as well as the Python-side default: these are NOT NULL,
    # and several callers insert a repo with raw SQL that names only the
    # columns it cares about. Without a database-side default that insert
    # fails outright rather than getting an empty object.
    enabled_categories: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default=sa.text("'{}'::jsonb")
    )
    # {category_key: {"assurance": int, "resolution": int}} overriding the
    # registry's own defaults, per-repo (plan.md §7.1's threshold sliders).
    thresholds: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=sa.text("'{}'::jsonb"))
    # Autonomous | balanced | verbose (plan.md §10.5) -- see app/ask_mode.py.
    ask_mode: Mapped[str] = mapped_column(sa.String, default="balanced", server_default="balanced")
    # The visible kill switch (plan.md §7.1) -- not decoration: a runaway
    # detector has to be stoppable in one click, per-repo.
    detection_paused: Mapped[bool] = mapped_column(sa.Boolean, default=False, server_default=sa.false())
    proposals_paused: Mapped[bool] = mapped_column(sa.Boolean, default=False, server_default=sa.false())
    # Per-repo Pages project. Empty falls back to CLOUDFLARE_PAGES_PROJECT
    # so an existing single-project deployment keeps working.
    cloudflare_pages_project: Mapped[str | None] = mapped_column(sa.String, nullable=True)
    # No Slack fields here on purpose: Slack is connected ONCE for the
    # account and every repo notifies the same channel (app/app_settings.py).
    # A per-repo channel made the user repeat an OAuth round-trip for every
    # repository and bought nothing.
    connected_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.func.now())


class WebhookDelivery(Base):
    """Durable GitHub webhook idempotency (X-GitHub-Delivery).

    The in-memory set in routers/webhooks.py is still consulted first; this
    table is what survives a process restart and a second replica.
    """

    __tablename__ = "webhook_deliveries"

    id: Mapped[uuid.UUID] = _uuid_pk()
    delivery_id: Mapped[str] = mapped_column(sa.String, nullable=False, unique=True)
    event: Mapped[str] = mapped_column(sa.String, default="")
    received_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.func.now())


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
    # Who owns this finding, and WHY. The reasoning is stored rather than
    # recomputed because routing rules change: a year from now, "why did this
    # reach me" must be answerable from the record, not from today's config.
    assignee_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True
    )
    assignment_reasoning: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # Notified because the blast radius touches their area -- not on the hook.
    watcher_user_ids: Mapped[list] = mapped_column(JSONB, default=list)
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
    # The proposed patch itself, persisted rather than left in the worktree.
    #
    # Nothing is pushed to GitHub until a human approves, so the PR body is no
    # longer where the reviewer reads the diff -- this column is. It also
    # survives the worktree, which is deleted on merge and rebuilt on demand,
    # so a superseded attempt stays readable long after its checkout is gone.
    diff: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    # 1 for the council's first proposal, incremented on every revision the
    # human asks for. Also the cap the revision limit is enforced against.
    attempt: Mapped[int] = mapped_column(sa.Integer, default=1, server_default="1")
    # The attempt that replaced this one, when a human asked for a different
    # approach. Null for the current attempt and for terminal outcomes.
    superseded_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("fixes.id"), nullable=True
    )
    # Free text from whoever rejected or asked for changes. The single most
    # valuable signal in the system and, until now, the one thing a boolean
    # approve/reject threw away.
    decision_note: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
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


class FixReview(Base):
    """One review conversation about one issue's fix, spanning every attempt.

    The thread outlives individual Fix rows on purpose. Asking for a different
    approach produces a NEW Fix (new patch, new sandbox verification, new
    adversarial score) while the conversation that led there continues -- so
    attempt 3 can be prompted with everything the human said about attempts 1
    and 2, not just the most recent sentence.

    Modelled on the sibling `opencode` deployment's PlanningSession, which
    solved the same problem: a durable transcript plus a status that says
    precisely who the system is waiting on, so a reply arriving twice cannot
    start two concurrent turns against the same thread.
    """

    __tablename__ = "fix_reviews"

    id: Mapped[uuid.UUID] = _uuid_pk()
    # One live thread per issue, enforced in the database rather than by
    # convention -- two threads about the same issue would each hold half the
    # context and neither would be right.
    issue_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("issues.id"), unique=True, index=True
    )
    # The attempt currently on the table. Every superseded attempt is still
    # reachable through Fix.issue_id; this is just which one to render.
    current_fix_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("fixes.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(sa.String, default=FixReviewStatus.AWAITING_DECISION.value)
    # [{role: "council"|"human"|"system", text, at, kind?, fix_id?}]
    # `kind` marks a turn that is not ordinary chat -- "proposal",
    # "revision-request", "decision" -- so the UI can render the exchange
    # without guessing from the role alone.
    transcript: Mapped[list] = mapped_column(JSONB, default=list)
    attempts: Mapped[int] = mapped_column(sa.Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.func.now())
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now()
    )


class SlackInstallation(Base):
    """One workspace's install of the Slack app, per organization.

    Declared here rather than left as a hand-made table. This existed only as
    raw SQL in app/app_settings.py plus a table somebody had created by hand
    in production, so a fresh database came up without it and every read
    failed with `relation "slack_installations" does not exist`. Nothing in
    the schema should be invisible to `create_all`.

    `team_id` is unique and is the only thing identifying whose workspace an
    inbound button click came from -- a click from an unrecognised team is
    refused rather than applied against whichever install happens to be first
    in the table (app/routers/webhooks.py).
    """

    __tablename__ = "slack_installations"
    __table_args__ = (sa.UniqueConstraint("team_id", name="uq_slack_install"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("organizations.id"), index=True
    )
    team_id: Mapped[str] = mapped_column(sa.String, nullable=False)
    team_name: Mapped[str] = mapped_column(sa.String, default="", server_default="")
    bot_token: Mapped[str] = mapped_column(sa.Text, nullable=False)
    channel_id: Mapped[str] = mapped_column(sa.String, default="", server_default="")
    channel_name: Mapped[str] = mapped_column(sa.String, default="", server_default="")
    installed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True
    )
    installed_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now()
    )
    # Set rather than deleted, so an uninstall keeps its audit trail and the
    # lookups filter on `revoked_at IS NULL`.
    revoked_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)


class OrgInvite(Base):
    """An outstanding invitation to join one organization.

    Deliberately NOT the same thing as an AccessRequest. An access request is
    someone asking the PLATFORM for an account and a system admin deciding;
    this is an org admin adding a colleague to a team that already exists. The
    two were conflated at first, which left every approved user sitting in no
    organization at all -- the gap this table closes.

    Carries the role, seniority and designations the admin chose at invite
    time, so accepting produces a fully-configured member rather than someone
    who then has to be set up a second time.
    """

    __tablename__ = "org_invites"
    __table_args__ = (
        # One LIVE invite per email per org. Enforced as a partial unique
        # index rather than a plain constraint: a revoked or accepted invite
        # to the same address must not block a fresh one.
        sa.Index(
            "uq_org_invite_pending",
            "org_id",
            "email",
            unique=True,
            postgresql_where=sa.text("status = 'pending'"),
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("organizations.id"), index=True
    )
    email: Mapped[str] = mapped_column(sa.String, nullable=False, index=True)
    name: Mapped[str] = mapped_column(sa.String, default="", server_default="")
    role: Mapped[OrgRole] = mapped_column(sa.Enum(OrgRole, name="org_role"), default=OrgRole.MEMBER)
    seniority: Mapped[Seniority] = mapped_column(sa.Enum(Seniority, name="seniority"), default=Seniority.SDE2)
    designation_keys: Mapped[list] = mapped_column(JSONB, default=list)
    invited_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True
    )
    # pending | accepted | revoked
    status: Mapped[str] = mapped_column(sa.String, default="pending", server_default="pending")
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.func.now())
    accepted_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)


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
    # Line span, so a dense hit can be recognised as the SAME piece of code
    # as a lexical or structural hit covering the same lines. Fusion
    # (app/hybrid_retrieval.py) merges candidates on span overlap; without
    # spans the dense channel could only be merged by exact symbol-name
    # equality, and the two indexes chunk at slightly different boundaries,
    # so the same function would reach the model twice.
    start_line: Mapped[int] = mapped_column(sa.Integer, server_default="1")
    end_line: Mapped[int] = mapped_column(sa.Integer, server_default="1")
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


class MemoryTrace(Base):
    """What was already tried here, and how it failed.

    Everything this system persisted before was a success artifact or a
    status. The question a later run most wants answered -- *what did we
    already attempt on this bug, and why didn't it work* -- was recoverable
    only by reading prose in the activity feed, which no council node does
    and no cheap model should be asked to.

    Every field here is already computed somewhere today and then thrown
    away: the detector has the failing command and its exit code, the
    Arbiter has the verdict, the outcome check has the mismatch. This records
    them as queryable rows instead of as log lines.
    """

    __tablename__ = "memory_traces"

    id: Mapped[uuid.UUID] = _uuid_pk()
    repo_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), sa.ForeignKey("repos.id"), index=True)
    issue_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), sa.ForeignKey("issues.id"), nullable=True)
    fix_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), sa.ForeignKey("fixes.id"), nullable=True)
    # The run that wrote this. Search EXCLUDES the calling run's own traces:
    # otherwise a stuck run retrieves its own echo -- the trace it wrote
    # thirty seconds ago comes back as "what we tried before", and the loop
    # it is stuck in becomes the evidence for staying in it.
    council_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    category: Mapped[str] = mapped_column(sa.String, default="")
    stage: Mapped[str] = mapped_column(sa.String, default="")  # detect | patch | verify | deploy | outcome
    outcome: Mapped[str] = mapped_column(sa.String)            # failed | rejected | regressed | abandoned
    attempt: Mapped[int] = mapped_column(sa.Integer, default=1)
    failing_command: Mapped[str] = mapped_column(sa.Text, default="")
    exit_code: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    touched_paths: Mapped[list] = mapped_column(JSONB, default=list)
    detail: Mapped[str] = mapped_column(sa.Text, default="")
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.func.now())


class LlmResponseCache(Base):
    """Exact-input response cache for the deterministic council calls
    (plan.md §13.2 -- referenced by the plan since the beginning, never
    actually built until now).

    Azure's own prompt cache is the wrong tool for a call whose ENTIRE input
    repeats: it discounts the input tokens and still bills the output and
    still makes the caller wait for it. This returns the previous answer
    immediately, for free.

    Opt-in at the call site, never blanket. A patch-generation call must
    never be served from here -- if the same rejected diff comes back a
    second time, the identical input is itself the signal that something is
    looping, and a cache hit would erase it.
    """

    __tablename__ = "llm_response_cache"

    id: Mapped[uuid.UUID] = _uuid_pk()
    cache_key: Mapped[str] = mapped_column(sa.String, nullable=False, unique=True)
    node: Mapped[str] = mapped_column(sa.String, default="")
    deployment: Mapped[str] = mapped_column(sa.String, default="")
    response_text: Mapped[str] = mapped_column(sa.Text)
    hit_count: Mapped[int] = mapped_column(sa.Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.func.now())
    expires_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)


class WorkItem(Base):
    """The one-way arrow between the web process and the privileged worker
    (plan.md §15).

    The dashboard renders model-authored content and untrusted repository
    text by definition -- that is its job -- and a process that does that
    while ALSO holding the Docker socket, the deploy credentials and a
    checkout of arbitrary third-party code is one RCE away from compromising
    everything this system touches. So the two are separate processes, and
    the dashboard's entire power over the privileged side is to insert a row
    here saying what was asked for. Nothing listens on the worker's side; it
    polls, and it decides for itself what to do with what it finds.

    `claimed_by` plus SKIP LOCKED claiming means several workers can run
    against one queue without ever handing the same item to two of them.
    """

    __tablename__ = "work_items"

    id: Mapped[uuid.UUID] = _uuid_pk()
    kind: Mapped[str] = mapped_column(sa.String, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(sa.String, default="queued", index=True)  # queued|running|done|failed
    attempts: Mapped[int] = mapped_column(sa.Integer, default=0)
    claimed_by: Mapped[str | None] = mapped_column(sa.String, nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    # Whatever the handler returned. The worker is a different PROCESS, so a
    # module-level global (which is how the eval report used to be kept)
    # cannot carry a result back to the web side at all -- it has to land
    # somewhere both halves can read.
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.func.now())


class AppSetting(Base):
    """Account-level configuration that both processes must agree on.

    Deliberately the DATABASE and not a rewrite of `.env`, which is how the
    Slack bot token used to be persisted. Since the security split there are
    two processes: the web process handles the OAuth callback, but the
    WORKER is what actually posts to Slack. A value written into .env by one
    container is not visible to the other until it restarts, so the approval
    message would keep going to the old channel -- or nowhere -- with nothing
    to indicate why. A row both read at send time cannot drift.
    """

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(sa.String, primary_key=True)
    value: Mapped[str] = mapped_column(sa.Text, default="")
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now()
    )


class CounselConversation(Base):
    """One chat thread with Counsel. Scoped to its owner -- a conversation
    can quote code and issue detail, so it is never shared implicitly."""

    __tablename__ = "counsel_conversations"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), sa.ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(sa.String, default="")
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.func.now())


class CounselMessage(Base):
    """A turn. Tool results are deliberately NOT stored: they are large, they
    go stale the moment a fix lands, and replaying a stale file into a later
    question is worse than re-reading the current one."""

    __tablename__ = "counsel_messages"

    id: Mapped[uuid.UUID] = _uuid_pk()
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("counsel_conversations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(sa.String)  # user | assistant
    content: Mapped[str] = mapped_column(sa.Text)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.func.now())


class Organization(Base):
    """The tenant.

    Everything a tenant owns hangs off a Repo, and Repo is the only table
    that carries `org_id` directly. Issues, fixes, traces, chunks and symbols
    all reach their org THROUGH their repo, which means tenancy has exactly
    one source of truth rather than a denormalised column on twelve tables
    that can drift out of sync with each other.
    """

    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(sa.String, nullable=False)
    slug: Mapped[str] = mapped_column(sa.String, nullable=False, unique=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.func.now())
    # GitHub App installation for THIS org. Empty falls back to the process-
    # wide GITHUB_APP_INSTALLATION_ID so a single-install deployment is
    # unchanged.
    github_app_installation_id: Mapped[str | None] = mapped_column(sa.String, nullable=True)


class OrgMember(Base):
    """One person's membership of one organization.

    Three orthogonal axes live here, and keeping them apart is the point:
    `role` is permission, `seniority` is what they can be escalated to, and
    designations (the join table below) are what they know. Promotion to
    org_admin touches only `role`.
    """

    __tablename__ = "org_members"
    __table_args__ = (sa.UniqueConstraint("org_id", "user_id", name="uq_org_member"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), sa.ForeignKey("organizations.id"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), sa.ForeignKey("users.id"), index=True)
    role: Mapped[OrgRole] = mapped_column(sa.Enum(OrgRole, name="org_role"), default=OrgRole.MEMBER)
    seniority: Mapped[Seniority] = mapped_column(sa.Enum(Seniority, name="seniority"), default=Seniority.SDE2)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.func.now())


class Designation(Base):
    """An area of expertise -- frontend, backend, devops, security.

    Org-configurable rather than a hardcoded enum, because every company
    names these differently and a fixed list guarantees a support request in
    week two.
    """

    __tablename__ = "designations"
    __table_args__ = (sa.UniqueConstraint("org_id", "key", name="uq_designation_key"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), sa.ForeignKey("organizations.id"), index=True)
    key: Mapped[str] = mapped_column(sa.String)
    label: Mapped[str] = mapped_column(sa.String)


class MemberDesignation(Base):
    """Many-to-many: one person can hold several areas, and most do."""

    __tablename__ = "member_designations"
    __table_args__ = (sa.UniqueConstraint("member_id", "designation_id", name="uq_member_designation"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    member_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), sa.ForeignKey("org_members.id", ondelete="CASCADE"), index=True)
    designation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), sa.ForeignKey("designations.id", ondelete="CASCADE"))


class RoutingRule(Base):
    """Which designation owns which category, and the seniority floor a
    severity demands.

    Configurable per org rather than hardcoded: "security findings go to the
    security team" is true everywhere, but which team that IS, and how senior
    someone must be to own a critical one, is a decision only the org can
    make.
    """

    __tablename__ = "routing_rules"
    __table_args__ = (sa.UniqueConstraint("org_id", "category", name="uq_routing_rule"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), sa.ForeignKey("organizations.id"), index=True)
    category: Mapped[str] = mapped_column(sa.String)
    designation_key: Mapped[str] = mapped_column(sa.String)
    # Severity at or above this demands the seniority below. A critical
    # security finding should not sit with an SDE1.
    escalate_at_severity: Mapped[int] = mapped_column(sa.Integer, default=4)
    min_seniority: Mapped[Seniority] = mapped_column(sa.Enum(Seniority, name="seniority"), default=Seniority.SDE2)


class IdentityLink(Base):
    """Maps a git identity to a person.

    The messy part of attribution: GitHub noreply addresses, personal versus
    work email, and people who have left. An unmatched author must fall
    through to designation routing, never block assignment.
    """

    __tablename__ = "identity_links"
    __table_args__ = (sa.UniqueConstraint("org_id", "provider", "external_id", name="uq_identity_link"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), sa.ForeignKey("organizations.id"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), sa.ForeignKey("users.id"), index=True)
    provider: Mapped[str] = mapped_column(sa.String)  # github | git-email | slack
    external_id: Mapped[str] = mapped_column(sa.String)


class ResearchFinding(Base):
    """One curated web-research claim, kept or dropped.

    Both halves are written (`kept` says which), because "we looked and decided
    not to use this" is a different state from "we never looked", and only the
    first one tells the next reader anything. The row carries the URL the claim
    was attributed to and the queries that found it, so a claim in a PRD can be
    traced back to a source the same way a code claim is traced to `path:line`.
    """

    __tablename__ = "research_findings"

    id: Mapped[uuid.UUID] = _uuid_pk()
    repo_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("repos.id"), nullable=True, index=True
    )
    issue_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("issues.id"), nullable=True, index=True
    )
    # Who asked: prd_research, patch_worker, counsel, ...
    role: Mapped[str] = mapped_column(sa.String, default="")
    question: Mapped[str] = mapped_column(sa.Text)
    claim: Mapped[str] = mapped_column(sa.Text)
    source_url: Mapped[str] = mapped_column(sa.Text, default="")
    source_title: Mapped[str] = mapped_column(sa.String, default="")
    relevance: Mapped[int] = mapped_column(sa.Integer, default=0)
    recency: Mapped[str] = mapped_column(sa.String, default="unknown")
    authority: Mapped[str] = mapped_column(sa.String, default="unverified")
    kept: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    reason: Mapped[str] = mapped_column(sa.Text, default="")
    queries: Mapped[list] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.func.now())


class PushSubscription(Base):
    """One browser's Web Push endpoint, bound to one person.

    Keyed on `endpoint`, which is unique, and that is what makes re-POSTing a
    subscription transfer it rather than duplicate it. That matters more than
    it looks: a push subscription belongs to the ORIGIN and the service worker
    registration, not to a login session. It survives logout and account
    switches. Bind it once at subscribe time and never again, and a device
    that subscribed as one person keeps delivering to that person forever,
    while the settings toggle -- which only reads browser state -- cheerfully
    reports that notifications are on.

    A sibling project lost six days of notifications on one phone to exactly
    that. `app/routers/push.py` re-binds on every authenticated session, which
    self-heals an already-wrong device with no migration.
    """

    __tablename__ = "push_subscriptions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    endpoint: Mapped[str] = mapped_column(sa.Text, unique=True)
    p256dh: Mapped[str] = mapped_column(sa.String)
    auth: Mapped[str] = mapped_column(sa.String)
    # Whose push service this is -- web.push.apple.com, fcm.googleapis.com.
    # Derived once at write time so "which platform is failing" is a query
    # rather than a string-parse over every row.
    provider: Mapped[str] = mapped_column(sa.String, default="")
    user_agent: Mapped[str] = mapped_column(sa.String, default="")
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.func.now())
    last_used_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
