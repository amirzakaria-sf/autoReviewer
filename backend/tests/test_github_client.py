from unittest.mock import MagicMock, patch

from app.integrations import github_client


def test_no_merge_capability_exists_on_the_client():
    """Literal test for the forbidden-action rule in plan.md §1: WhipGuard opens a
    pull request, never merges it. This must never become a method, not even one
    gated behind a flag."""
    assert not hasattr(github_client, "merge_pr")
    assert not hasattr(github_client, "merge")
    assert "merge" not in dir(github_client)


def test_create_issue_sends_labels_and_auth_header():
    fake_response = MagicMock()
    fake_response.json.return_value = {"number": 42}
    fake_response.raise_for_status.return_value = None

    with patch("app.integrations.github_client.httpx.post", return_value=fake_response) as mock_post:
        number = github_client.create_issue(
            "amirzakaria-sf/whipguard-demo-ui", "Delete removes wrong item", "body", ["whipguard:bug"]
        )

    assert number == 42
    _, kwargs = mock_post.call_args
    assert kwargs["json"]["labels"] == ["whipguard:bug"]
    assert kwargs["headers"]["Authorization"].startswith("Bearer ")


def test_create_draft_pr_is_marked_draft_and_never_sets_merge_method():
    pr_response = MagicMock()
    pr_response.json.return_value = {"number": 7}
    pr_response.raise_for_status.return_value = None
    label_response = MagicMock()
    label_response.raise_for_status.return_value = None

    with patch(
        "app.integrations.github_client.httpx.post", side_effect=[pr_response, label_response]
    ) as mock_post:
        number = github_client.create_draft_pr(
            "amirzakaria-sf/whipguard-demo-ui",
            "whipguard/1-fix-delete",
            "main",
            "Fix off-by-one",
            "Closes #1",
            ["whipguard:fix-proposed"],
        )

    assert number == 7
    first_call_kwargs = mock_post.call_args_list[0].kwargs
    assert first_call_kwargs["json"]["draft"] is True
    assert "merge_method" not in first_call_kwargs["json"]
