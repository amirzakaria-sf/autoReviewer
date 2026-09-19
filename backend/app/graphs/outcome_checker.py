"""The cross-app outcome checker (plan.md §11).

The plan verifies the fix. This checks that the systems agree with each other and
with the fix. Reads GitHub, Cloudflare, Slack, and the dashboard's own row back
independently and reduces them to one status. Any disagreement -> the run is
outcome-check-failed, even if every individual step upstream reported success.

Intentionally small and mechanical: four reads and one comparison, no model call —
this is the one piece of the whole system that must never be a matter of judgment.
"""

from __future__ import annotations

from typing import TypedDict


class OutcomeInputs(TypedDict):
    github_state: dict  # {"issue_exists": bool, "labels": list[str], "pr_open": bool, "pr_merged": bool, "closes_reference": bool}
    cloudflare_state: dict  # {"reachable": bool, "assertion_passes": bool}
    slack_state: dict  # {"status_text": str | None, "configured": bool}
    dashboard_state: str  # the Fix.status value the dashboard row currently shows


def check_outcome(inputs: OutcomeInputs) -> dict:
    """Returns {"agreed": bool, "resolved_status": str, "mismatch_detail": dict | None}."""

    github = inputs["github_state"]
    cloudflare = inputs["cloudflare_state"]
    slack = inputs["slack_state"]
    dashboard_status = inputs["dashboard_state"]

    mismatches: dict[str, str] = {}

    # GitHub: issue + PR must exist, correctly labeled, and NEVER merged.
    if github.get("pr_merged"):
        mismatches["github"] = "PR is merged — this must never happen (forbidden action)."
    elif not github.get("issue_exists") or not github.get("pr_open"):
        mismatches["github"] = "issue or PR is missing/closed when a verified fix was expected."
    elif not github.get("closes_reference"):
        mismatches["github"] = "PR body is missing the Closes #<issue-number> reference."

    # Cloudflare: the live URL must actually pass the same assertion that gated
    # the proposal — not merely respond with a non-error status code.
    if not cloudflare.get("reachable"):
        mismatches["cloudflare"] = "preview URL is not reachable."
    elif not cloudflare.get("assertion_passes"):
        mismatches["cloudflare"] = "live re-check of the gating assertion failed."

    # Slack: the thread's current text must reflect the current dashboard
    # status, not a stale message left over from an earlier state.
    #
    # A surface that is not CONFIGURED is not a surface that disagrees. With
    # Slack disconnected there is no thread to read, `status_text` comes back
    # None, and the old check read that as a contradiction -- so every
    # otherwise-perfect run failed closed on the absence of an integration
    # nobody had set up. Found on a real approval where GitHub, Cloudflare
    # and the dashboard all agreed and this was the only objection.
    #
    # `configured` is set by the caller, which knows whether a bot token and
    # channel exist. Absent (an older payload), fall back to treating a
    # missing thread as not-configured rather than as a mismatch: silence is
    # ambiguous, and failing a verified deploy on an ambiguity is the more
    # expensive mistake.
    slack_configured = slack.get("configured")
    if slack_configured is None:
        slack_configured = slack.get("status_text") is not None

    if slack_configured:
        slack_text = (slack.get("status_text") or "").lower()
        if dashboard_status.lower() not in slack_text:
            mismatches["slack"] = (
                f"thread text does not mention current status {dashboard_status!r} "
                f"(saw: {slack.get('status_text')!r})."
            )

    agreed = not mismatches
    resolved_status = dashboard_status if agreed else "outcome-check-failed"

    return {
        "agreed": agreed,
        "resolved_status": resolved_status,
        "mismatch_detail": mismatches or None,
    }
