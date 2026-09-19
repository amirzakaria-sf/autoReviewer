from app.graphs.outcome_checker import check_outcome

AGREEING_INPUTS = {
    "github_state": {"issue_exists": True, "labels": ["whipguard:verified"], "pr_open": True, "pr_merged": False, "closes_reference": True},
    "cloudflare_state": {"reachable": True, "assertion_passes": True},
    "slack_state": {"status_text": "Status: verified :white_check_mark:"},
    "dashboard_state": "verified",
}


def test_all_four_agreeing_states_pass():
    result = check_outcome(AGREEING_INPUTS)
    assert result["agreed"] is True
    assert result["resolved_status"] == "verified"
    assert result["mismatch_detail"] is None


def test_stale_slack_message_fails_closed_even_though_three_of_four_are_fine():
    """The test to show a judge if only one test gets shown: Slack says a stale
    'we shipped it' while GitHub/Cloudflare/dashboard are all individually fine —
    the run must still be reported as outcome-check-failed, not a false 'verified'."""
    inputs = {
        **AGREEING_INPUTS,
        "slack_state": {"status_text": "Status: awaiting-approval"},
    }

    result = check_outcome(inputs)

    assert result["agreed"] is False
    assert result["resolved_status"] == "outcome-check-failed"
    assert "slack" in result["mismatch_detail"]
    assert "github" not in result["mismatch_detail"]
    assert "cloudflare" not in result["mismatch_detail"]


def test_merged_pr_is_always_a_failure_forbidden_action():
    inputs = {**AGREEING_INPUTS, "github_state": {**AGREEING_INPUTS["github_state"], "pr_merged": True}}
    result = check_outcome(inputs)
    assert result["agreed"] is False
    assert "github" in result["mismatch_detail"]


def test_cloudflare_reachable_but_assertion_failing_is_not_a_pass():
    """A 200-OK check alone is not enough — the same gating assertion must
    actually still pass against the live URL."""
    inputs = {**AGREEING_INPUTS, "cloudflare_state": {"reachable": True, "assertion_passes": False}}
    result = check_outcome(inputs)
    assert result["agreed"] is False
    assert "cloudflare" in result["mismatch_detail"]


def test_missing_closes_reference_fails():
    inputs = {
        **AGREEING_INPUTS,
        "github_state": {**AGREEING_INPUTS["github_state"], "closes_reference": False},
    }
    result = check_outcome(inputs)
    assert result["agreed"] is False
    assert "github" in result["mismatch_detail"]


def _agreeing_inputs(**overrides):
    base = {
        "github_state": {
            "issue_exists": True, "labels": [], "pr_open": True,
            "pr_merged": False, "closes_reference": True,
        },
        "cloudflare_state": {"reachable": True, "assertion_passes": True},
        "slack_state": {"status_text": None, "configured": False},
        "dashboard_state": "verified",
    }
    base.update(overrides)
    return base


def test_an_unconnected_slack_is_not_a_disagreement():
    """The checker fails a run closed on ANY disagreement, so counting the
    absence of an integration nobody set up as one meant every deploy failed
    for anyone without Slack. Found on a real approval where GitHub,
    Cloudflare and the dashboard all agreed and this was the sole objection."""
    result = check_outcome(_agreeing_inputs())

    assert result["agreed"] is True
    assert result["resolved_status"] == "verified"


def test_a_connected_slack_showing_a_stale_status_still_fails():
    """The check has to keep working where it applies -- the point of reading
    four systems back is catching the one that quietly drifted."""
    result = check_outcome(
        _agreeing_inputs(slack_state={"status_text": "Status: *Deployed*", "configured": True})
    )

    assert result["agreed"] is False
    assert "slack" in result["mismatch_detail"]


def test_slack_that_cannot_be_read_is_treated_as_absent_not_contradictory():
    result = check_outcome(_agreeing_inputs(slack_state={"status_text": None, "configured": True}))

    assert result["agreed"] is False, "a configured thread that reads back empty IS a mismatch"


def test_an_older_payload_without_the_configured_flag_still_behaves():
    """Items queued before this field existed must not fail closed on a key
    they never carried."""
    result = check_outcome(_agreeing_inputs(slack_state={"status_text": None}))

    assert result["agreed"] is True
