from app.enums import FIX_STATUS_RENDER, ISSUE_STATUS_RENDER, FixStatus, IssueStatus


def test_every_issue_status_has_a_render_entry():
    for member in IssueStatus:
        assert member in ISSUE_STATUS_RENDER, f"{member} missing from ISSUE_STATUS_RENDER"


def test_every_fix_status_has_a_render_entry():
    for member in FixStatus:
        assert member in FIX_STATUS_RENDER, f"{member} missing from FIX_STATUS_RENDER"


def test_github_labels_are_unique_and_prefixed():
    labels = [entry["github_label"] for entry in ISSUE_STATUS_RENDER.values()]
    labels += [entry["github_label"] for entry in FIX_STATUS_RENDER.values()]

    assert len(labels) == len(set(labels)), "two statuses collide on the same GitHub label"
    for label in labels:
        assert label.startswith("whipguard:"), f"{label!r} does not start with 'whipguard:'"
