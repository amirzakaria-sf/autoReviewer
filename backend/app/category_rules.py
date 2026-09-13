"""The `ui` category's rules blob, loaded once as real content (plan.md §9.4).

This exists so `test_prompts.py::test_oversized_rules_fail_not_truncate` has real
content to size a too-small budget against, and so the stable prefix is built from
actual category rules rather than a placeholder string.
"""

UI_CATEGORY_RULES = """\
Category: ui

Detector: Playwright (Chromium only for Tier 0).
A candidate issue is a failing assertion in the repo's own Playwright suite, run
headlessly against the current branch. Evidence attached: the failing assertion
text, a trace, and a screenshot at the point of failure.

Fixer scope: frontend files only (paths matched by the repo's own frontend glob —
for the fixture repo, everything except playwright.config.ts and tests/).

Thresholds: assurance >= 75 raises a GitHub issue. resolution >= 80 opens a draft PR.

Skeptic's job: argue the failing assertion is a flake, an intentional behavior
change, or already covered elsewhere — never "do you agree," always "what would
have to be true for this to be wrong, and is it."

Mechanical recheck: the exact same Playwright spec is re-run, right now, not from
any cache. A flake that passes on rerun is dropped here, in code, before any model
sees it a second time.

Verifier's job (Fix Council): actually build the patched branch and run the full
Playwright suite against a throwaway local preview. Report what happened, not an
opinion about what should have happened.
"""
