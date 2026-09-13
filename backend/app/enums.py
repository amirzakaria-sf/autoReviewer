from enum import Enum


class IssueStatus(str, Enum):
    DETECTED_BELOW_THRESHOLD = "detected-below-threshold"
    RAISED = "raised"
    FIX_PROPOSED = "fix-proposed"
    CLOSED = "closed"


class FixStatus(str, Enum):
    AWAITING_APPROVAL = "awaiting-approval"
    APPROVED = "approved"
    IN_PROGRESS = "in-progress"
    DEPLOYED = "deployed"
    VERIFIED = "verified"
    VERIFICATION_FAILED = "verification-failed"
    OUTCOME_CHECK_FAILED = "outcome-check-failed"
    REJECTED = "rejected"


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
}
