from enum import Enum


class UserRole(str, Enum):
    ADMIN = "admin"
    MEMBER = "member"


class UserStatus(str, Enum):
    # A User row is only ever created already-ACTIVE now -- either the
    # bootstrap admin at startup, or a real person completing an approved
    # AccessRequest's invite link. No User exists in a pending state; a
    # request that hasn't been decided yet has no User row at all (see
    # AccessRequestStatus below). ACTIVE is the only status a fresh row is
    # ever created with; the others exist for an admin to deactivate someone
    # later.
    ACTIVE = "active"
    DEACTIVATED = "deactivated"


class AccessRequestStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class IssueStatus(str, Enum):
    DETECTED_BELOW_THRESHOLD = "detected-below-threshold"
    RAISED = "raised"
    FIX_PROPOSED = "fix-proposed"
    CLOSED = "closed"
    # The Arbiter returned needs_clarification instead of a forced score
    # (plan.md §10.5) -- held here until a human answers the
    # HumanInputRequest, then the Arbiter runs again with that answer folded
    # into context.
    AWAITING_CLARIFICATION = "awaiting-clarification"


class FixStatus(str, Enum):
    AWAITING_APPROVAL = "awaiting-approval"
    APPROVED = "approved"
    IN_PROGRESS = "in-progress"
    DEPLOYED = "deployed"
    VERIFIED = "verified"
    VERIFICATION_FAILED = "verification-failed"
    OUTCOME_CHECK_FAILED = "outcome-check-failed"
    REJECTED = "rejected"
    # A human merged the PR directly on GitHub -- WhipGuard itself never does
    # this (no merge_pr capability exists, tested directly), but the dashboard
    # still has to reflect reality once it happens, via the pull_request
    # webhook (routers/webhooks.py), not stay stuck on a stale "verified".
    MERGED = "merged"


# The one place status ever becomes a GitHub label or a dashboard badge string.
# Every render surface (GitHub labels, dashboard badges, Slack text) reads from
# these dicts — never re-derives its own copy of the state machine.
ISSUE_STATUS_RENDER: dict[IssueStatus, dict] = {
    IssueStatus.DETECTED_BELOW_THRESHOLD: {
        "github_label": "whipguard:detected-below-threshold",
        "dashboard_badge": "Detected (below threshold)",
        "dashboard_color": "gray",
    },
    IssueStatus.RAISED: {
        "github_label": "whipguard:bug",
        "dashboard_badge": "Raised",
        "dashboard_color": "blue",
    },
    IssueStatus.FIX_PROPOSED: {
        "github_label": "whipguard:fix-proposed",
        "dashboard_badge": "Fix proposed",
        "dashboard_color": "yellow",
    },
    IssueStatus.CLOSED: {
        "github_label": "whipguard:closed",
        "dashboard_badge": "Closed",
        "dashboard_color": "gray",
    },
    IssueStatus.AWAITING_CLARIFICATION: {
        "github_label": "whipguard:awaiting-clarification",
        "dashboard_badge": "Awaiting your answer",
        "dashboard_color": "yellow",
    },
}

FIX_STATUS_RENDER: dict[FixStatus, dict] = {
    FixStatus.AWAITING_APPROVAL: {
        "github_label": "whipguard:awaiting-approval",
        "dashboard_badge": "Awaiting approval",
        "dashboard_color": "yellow",
    },
    FixStatus.APPROVED: {
        "github_label": "whipguard:approved",
        "dashboard_badge": "Approved",
        "dashboard_color": "blue",
    },
    FixStatus.IN_PROGRESS: {
        "github_label": "whipguard:in-progress",
        "dashboard_badge": "In progress",
        "dashboard_color": "blue",
    },
    FixStatus.DEPLOYED: {
        "github_label": "whipguard:deployed",
        "dashboard_badge": "Deployed",
        "dashboard_color": "blue",
    },
    FixStatus.VERIFIED: {
        "github_label": "whipguard:verified",
        "dashboard_badge": "Verified",
        "dashboard_color": "green",
    },
    FixStatus.VERIFICATION_FAILED: {
        "github_label": "whipguard:verification-failed",
        "dashboard_badge": "Verification failed",
        "dashboard_color": "red",
    },
    FixStatus.OUTCOME_CHECK_FAILED: {
        "github_label": "whipguard:outcome-check-failed",
        "dashboard_badge": "Outcome check failed",
        "dashboard_color": "red",
    },
    FixStatus.REJECTED: {
        "github_label": "whipguard:rejected",
        "dashboard_badge": "Rejected",
        "dashboard_color": "gray",
    },
    FixStatus.MERGED: {
        "github_label": "whipguard:merged",
        "dashboard_badge": "Merged (by human)",
        "dashboard_color": "green",
    },
}
