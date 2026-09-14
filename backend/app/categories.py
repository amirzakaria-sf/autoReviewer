"""The category registry (plan.md §2): every category is a config row, not a
code branch. Adding one later is data entry — a new row here plus a detector
module implementing the `Detector` protocol, never a new subsystem.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class DetectionResult:
    failed: bool
    assertion_text: str
    stderr: str = ""
    extra: dict | None = None


class Detector(Protocol):
    def run(self, worktree_path: str) -> DetectionResult: ...


@dataclass(frozen=True)
class CategoryConfig:
    key: str
    label: str
    detector_tools: str
    scope_glob: re.Pattern
    assurance_threshold: int
    resolution_threshold: int
    rules: str
    entry_files: tuple[str, ...]  # RetrievalNode's one-hop expansion starts here


UI_RULES = """\
Category: ui

Detector: Playwright (Chromium).
A candidate issue is a failing assertion in the repo's own Playwright suite, run
headlessly against the current branch. Evidence attached: the failing assertion
text, a trace, and a screenshot at the point of failure.

Fixer scope: frontend files only.

Skeptic's job: argue the failing assertion is a flake, an intentional behavior
change, or already covered elsewhere.

Mechanical recheck: the exact same Playwright spec is re-run, right now, not
from any cache. A flake that passes on rerun is dropped here, in code.
"""

BACKEND_RULES = """\
Category: backend

Detector: the repo's own test suite (node --test, pytest, etc.), run headlessly.
A candidate issue is a failing test assertion. Evidence attached: the failing
test's name, the assertion diff, and stderr.

Fixer scope: backend files only (server-side logic, not frontend markup/CSS).

Skeptic's job: argue the failing test is asserting the wrong thing, is
environment-dependent, or is already known-flaky.

Mechanical recheck: the exact same test command is re-run, right now, not from
any cache.
"""

SECURITY_RULES = """\
Category: security

Detector: a gitleaks-style static secret scan (AWS keys, generic API-key/token
assignments, PEM private key blocks) over every tracked text file. A candidate
issue is a match against a known secret shape. Evidence attached: the file,
line number, and matched pattern label (never the full secret value itself in
any evidence that gets logged or displayed).

Fixer scope: any file, but the diff should be as narrow as possible -- remove
or rotate the specific secret, not a broad refactor.

Skeptic's job: argue the match is a false positive (an obviously-fake
placeholder value, a test fixture explicitly documented as fake, a string that
merely LOOKS like a secret shape).

Security gets the highest thresholds on purpose (plan.md §2): a false
positive here costs a wasted review; a false negative costs much more.
"""

PERFORMANCE_RULES = """\
Category: performance

Detector: a bundle-size budget check over shipped JS. A candidate issue is
total shipped JS exceeding the configured budget. Evidence attached: the
per-file size breakdown and the amount over budget.

Fixer scope: backend or frontend, whichever file grew.

Skeptic's job: argue the size increase is justified (a real new feature's
code, not bloat) or that the budget itself is miscalibrated for what the repo
now legitimately needs to ship.
"""

DOCUMENTATION_RULES = """\
Category: documentation

Detector: a doc-vs-code drift check -- every `functionName(` reference inside
README.md's inline code spans must name a real symbol that actually exists in
the source. A candidate issue is a referenced function that doesn't exist
(renamed, removed, or a typo). Evidence attached: the drifted reference(s) and
the real symbol names found in the source.

Fixer scope: documentation files only (README.md and other .md files) -- this
category never touches application code, only brings the docs back in sync
with what the code actually does.

Skeptic's job: argue the reference is intentional (documenting a planned,
not-yet-implemented function) rather than genuine drift.
"""

CATEGORY_REGISTRY: dict[str, CategoryConfig] = {
    "ui": CategoryConfig(
        key="ui",
        label="UI issues",
        detector_tools="Playwright",
        scope_glob=re.compile(r"^(playwright\.config\.ts|tests/|backend/)"),
        assurance_threshold=75,
        resolution_threshold=80,
        rules=UI_RULES,
        entry_files=("app.js",),
    ),
    "backend": CategoryConfig(
        key="backend",
        label="Backend issues",
        detector_tools="test run (node --test / pytest)",
        scope_glob=re.compile(r"^(playwright\.config\.ts|tests/|index\.html|style\.css|app\.js$)"),
        assurance_threshold=75,
        resolution_threshold=85,
        rules=BACKEND_RULES,
        entry_files=("backend/calculate.js",),
    ),
    "security": CategoryConfig(
        key="security",
        label="Security issues",
        detector_tools="secret scan (gitleaks-style)",
        # Deliberately permissive: security fixes can legitimately touch any
        # file (a secret could be anywhere), narrowly scoped by the diff
        # itself rather than a path glob.
        scope_glob=re.compile(r"^$"),
        assurance_threshold=85,
        resolution_threshold=90,
        rules=SECURITY_RULES,
        entry_files=("app.js", "backend/calculate.js"),
    ),
    "performance": CategoryConfig(
        key="performance",
        label="Performance issues",
        detector_tools="bundle-size budget check",
        scope_glob=re.compile(r"^(playwright\.config\.ts|tests/)"),
        assurance_threshold=70,
        resolution_threshold=80,
        rules=PERFORMANCE_RULES,
        entry_files=("app.js",),
    ),
    "documentation": CategoryConfig(
        key="documentation",
        label="Documentation issues",
        detector_tools="doc-vs-code drift check",
        # Docs only -- the inverse of the others: everything EXCEPT *.md is
        # out of scope.
        scope_glob=re.compile(r"^(?!.*\.md$).*$"),
        assurance_threshold=65,
        resolution_threshold=75,
        rules=DOCUMENTATION_RULES,
        entry_files=("README.md",),
    ),
}


def scope_excludes(category_key: str, relative_path: str) -> bool:
    """True if `relative_path` is OUTSIDE this category's write scope."""
    config = CATEGORY_REGISTRY[category_key]
    return bool(config.scope_glob.match(relative_path))


def enabled_categories_for(repo) -> list[str]:
    """Absent from Repo.enabled_categories means ON — a category is a config
    row the moment it's added to CATEGORY_REGISTRY, never something that has
    to be explicitly turned on first (plan.md §8's toggle matrix defaults)."""
    toggles = repo.enabled_categories or {}
    return [
        key
        for key in CATEGORY_REGISTRY
        if toggles.get(key, {}).get("issues", True)
    ]


def assurance_threshold_for(repo, category_key: str) -> int:
    override = (repo.thresholds or {}).get(category_key, {}).get("assurance")
    return override if override is not None else CATEGORY_REGISTRY[category_key].assurance_threshold


def resolution_threshold_for(repo, category_key: str) -> int:
    override = (repo.thresholds or {}).get(category_key, {}).get("resolution")
    return override if override is not None else CATEGORY_REGISTRY[category_key].resolution_threshold
