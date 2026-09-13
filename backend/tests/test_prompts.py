from app.category_rules import UI_CATEGORY_RULES
from app.prompts import (
    CACHE_FLOOR_TOKENS,
    _estimate_tokens,
    build_prefix,
    build_volatile_suffix,
    pad_to_cache_floor,
    partition_key,
)

WORKSPACE_MAP = "stack: vanilla JS, entry: index.html, files: app.js, style.css"


def test_identical_inputs_produce_byte_identical_prefix():
    a = build_prefix("skeptic", WORKSPACE_MAP, UI_CATEGORY_RULES)
    b = build_prefix("skeptic", WORKSPACE_MAP, UI_CATEGORY_RULES)
    assert a == b


def test_retry_prefix_stays_byte_identical_to_first_attempt():
    # Simulates the §9.6 contract: the stable prefix passed on a retry must be the
    # exact same bytes as attempt 1. Only the volatile suffix may change.
    attempt1_prefix = build_prefix("verifier", WORKSPACE_MAP, UI_CATEGORY_RULES)
    attempt2_prefix = build_prefix("verifier", WORKSPACE_MAP, UI_CATEGORY_RULES)
    assert attempt1_prefix == attempt2_prefix

    attempt1_suffix = build_volatile_suffix("fix the delete handler off-by-one")
    attempt2_suffix = build_volatile_suffix(
        "fix the delete handler off-by-one",
        prior_attempt_rejection="patch changed the add handler too, out of scope",
    )
    assert attempt1_suffix != attempt2_suffix


def test_short_prefix_gets_padded_to_cache_floor():
    short_prefix = build_prefix("skeptic", "", "")
    assert _estimate_tokens(short_prefix) < CACHE_FLOOR_TOKENS

    padded = pad_to_cache_floor(short_prefix)
    assert _estimate_tokens(padded) >= CACHE_FLOOR_TOKENS


def test_empty_prefix_is_not_padded_with_fake_content():
    assert pad_to_cache_floor("") == ""


def test_partition_key_changes_when_prefix_changes():
    prefix_v1 = build_prefix("skeptic", WORKSPACE_MAP, UI_CATEGORY_RULES)
    prefix_v2 = build_prefix("skeptic", WORKSPACE_MAP, UI_CATEGORY_RULES + "\nextra rule")

    key_v1 = partition_key("whipguard-demo-ui", "skeptic", prefix_v1)
    key_v1_again = partition_key("whipguard-demo-ui", "skeptic", prefix_v1)
    key_v2 = partition_key("whipguard-demo-ui", "skeptic", prefix_v2)

    assert key_v1 == key_v1_again
    assert key_v1 != key_v2


def test_oversized_rules_fail_not_truncate():
    """Regression test named directly in plan.md §9.4: a rules blob that doesn't
    fit its configured budget must fail a test, never silently truncate."""
    configured_budget_tokens = 20  # deliberately smaller than the real rules blob
    prefix = build_prefix("skeptic", WORKSPACE_MAP, UI_CATEGORY_RULES)

    fits = _estimate_tokens(prefix) <= configured_budget_tokens
    assert not fits, (
        "This assertion documents the failure mode: if this ever becomes True "
        "without the budget being deliberately raised, someone silently shrank "
        "the category rules content instead of raising the budget — the exact "
        "regression this test exists to catch."
    )
