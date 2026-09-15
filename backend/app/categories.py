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

## Detector
Playwright (Chromium), run headlessly against the current branch's real DOM --
never a snapshot diff, never a visual-regression pixel comparison. A candidate
issue is a failing assertion in the repo's own Playwright spec. Evidence
attached: the failing assertion text, a trace, and a screenshot at the point
of failure. The mechanical recheck re-runs the exact same spec, right now, not
from any cache -- a flake that passes on rerun is dropped in code before any
model sees it a second time.

## What counts as a UI bug here
- A user-visible action (click, type, submit) produces the wrong resulting
  DOM state -- wrong item removed, wrong total displayed, a list that doesn't
  re-render after a mutation.
- An element the spec asserts on is missing, disabled, or has the wrong text
  after an interaction that should have changed it.
- State that should persist across a re-render (or should reset and doesn't)
  behaves the other way.
This category is about **behavior**, not appearance -- color, spacing and
layout belong to a future accessibility/visual category, not this one.

## Severity guidance
- High: the core interaction the page exists for is broken (e.g. deleting
  removes the wrong row, checkout total is wrong).
- Medium: a secondary interaction is broken but the primary flow still works.
- Low: a cosmetic-adjacent behavior glitch with an easy workaround (e.g. a
  state resets one interaction later than expected).

## Skeptic's toolkit -- common false positives in THIS shape of finding
- A timing flake: the assertion ran before an async render/fetch settled, not
  because behavior is wrong -- look for a missing `await`/`waitFor` in the
  SPEC itself, not the app code, before blaming the app.
- The spec asserts on stale selectors after an intentional, recent markup
  change -- check `git log` on the asserted element before assuming the app
  regressed.
- Browser-only nondeterminism (animation timing, focus order) that the
  mechanical recheck already exists to catch -- if it fails ONE way but the
  recheck disagrees, that tension is real signal, cite it.

## Corroborator's toolkit -- what real evidence looks like
- The failing assertion's expected vs. actual values directly contradict each
  other in a way a screenshot or trace corroborates (not just the assertion
  string alone).
- The same DOM/state bug is reachable through more than one interaction path
  in the source, not a one-off.
- The mechanical recheck reproduced the SAME failure, not a different one --
  a different failure on rerun is itself worth flagging, not silently folded
  into the original finding.

## Fixer scope and constraints
Frontend files only. Do not touch backend/server-side logic to patch a UI
symptom whose actual cause is a backend bug -- that belongs to the `backend`
category; raise it there instead of reaching across scope.

## What a correct fix looks like
- Fixes the actual state transition (the event handler, the render
  condition), not the assertion that caught it.
- Reproduces cleanly against the SAME Playwright spec with no changes to that
  spec -- a fix that requires editing the test to pass is not a fix.
- Touches the smallest set of lines that changes the wrong behavior into the
  right one; do not refactor surrounding code the bug report didn't ask about.
"""

BACKEND_RULES = """\
Category: backend

## Detector
The repo's own test suite (node --test, pytest, or equivalent), run headlessly
in the sandbox. A candidate issue is a failing test assertion. Evidence
attached: the failing test's name, the assertion diff, and stderr. The
mechanical recheck re-runs the exact same command, right now, not from cache.

## What counts as a backend bug here
- A pure function or business-logic routine returns the wrong value for a
  documented or clearly-intended input (off-by-one, wrong operator, unhandled
  edge case like zero/negative/empty input).
- A computation that should be idempotent or commutative isn't, and the test
  demonstrates it.
- Server-side state mutation doesn't match what the test asserts should have
  happened (wrong record updated, wrong count persisted).
This category is about **server-side logic correctness**, not markup or CSS,
and not auth/input-validation/secret handling -- that's `security`.

## Severity guidance
- High: the function is on a path that affects money, quantity, or data
  integrity (e.g. a calculation used for a total or a count).
- Medium: wrong output on an edge case that's plausible but uncommon in
  practice (empty input, boundary value).
- Low: wrong output only on an input the surrounding code already guards
  against elsewhere, making the practical blast radius small.

## Skeptic's toolkit -- common false positives in THIS shape of finding
- The test itself encodes a wrong expectation (asserts the OLD, since-changed
  correct behavior) -- diff the test against recent commits before trusting
  it over the implementation.
- Environment-dependent failure (locale, timezone, floating-point precision)
  that isn't really a logic bug -- check whether the assertion tolerates
  floating-point at all.
- A test that's already flagged elsewhere as known-flaky (check for a
  `.skip`/`test.todo` sibling or a comment near the test) reintroduced without
  the skip -- that's a test-suite hygiene issue, not necessarily a fresh bug.

## Corroborator's toolkit -- what real evidence looks like
- The assertion diff shows a value that's wrong by a mechanism you can name
  from reading the source (e.g. `<` where `<=` was needed, an index that's
  off by exactly one).
- The same wrong value would occur for other inputs along the same code path,
  not just the one the test happened to pick.
- The mechanical recheck reproduced the identical wrong value, not merely
  "still fails" -- an identical wrong number is much stronger evidence than a
  bare pass/fail.

## Fixer scope and constraints
Backend files only (server-side logic) -- never frontend markup/CSS to patch
a backend symptom.

## What a correct fix looks like
- Fixes the actual computation/logic error at its source, not by special-
  casing the specific input the test uses.
- Passes the SAME test unmodified, and should not require weakening any
  other passing assertion in the same file to get there.
- Preserves the function's existing signature and side-effect contract unless
  the bug report specifically describes a contract mismatch.
"""

SECURITY_RULES = """\
Category: security

## Detector
A gitleaks-style static secret scan (AWS access-key-ID shape, generic
`api[_-]?key`/`secret`/`token` assignment patterns, PEM private-key blocks)
over every tracked text file in the worktree -- pure text scanning, no code
execution, so it runs directly on the checkout with no sandbox needed. A
candidate issue is a match against one of these known secret shapes. Evidence
attached: the file, line number, and matched pattern LABEL only -- never the
full matched secret value itself in any evidence that gets logged, displayed,
or included in a GitHub issue/PR body.

## What counts as a security finding here
- A real-looking credential, API key, or private key committed directly into
  tracked source (not `.env`, not a build artifact, not a lockfile hash).
- A hardcoded secret used directly in application code rather than read from
  configuration/environment at runtime.
This category currently covers committed-secret exposure specifically --
not injection, auth bypass, or dependency CVEs (this repo has no runtime
dependencies to audit; see the registry's own scope note before assuming a
broader security surface is being checked here than actually is).

## Severity guidance
- High (default): any match in application source that would work as a live
  credential if it were real -- correct key-length/shape, plausible-looking
  characters, not a documented placeholder.
- This category's thresholds are deliberately the highest of all five
  (assurance 85, resolution 90): a false positive here costs one wasted
  review; a false negative costs a live credential shipping to `main`.

## Skeptic's toolkit -- common false positives in THIS shape of finding
- A well-known, explicitly-documented placeholder value (e.g. AWS's own
  published example key `AKIAIOSFODNN7EXAMPLE`) -- these exist precisely so
  scanners and humans can recognize them; check the matched string against
  known placeholder patterns before treating it as live.
- A test fixture explicitly named/commented as fake (`test_api_key`,
  `dummy_secret`, a fixture file under a `tests/`/`fixtures/` path with a
  comment saying so).
- A string that merely matches the SHAPE (right length, right character
  class) without being presented as a credential anywhere nearby -- e.g. a
  hash, a UUID, or sample data that coincidentally looks key-shaped.

## Corroborator's toolkit -- what real evidence looks like
- The matched string sits in a variable/constant literally named `apiKey`,
  `secret`, `token`, `password`, or is passed directly as an `Authorization`
  header or SDK credential argument -- context confirms intended use as a
  real secret, not coincidental shape.
- The value is NOT a documented, publicly-known example/placeholder (verify
  against common placeholder lists before corroborating).
- The file is one that ships (application source, not a `.md` doc explaining
  what a key looks like, not a `.env.example` template with an obviously
  fake value).

## Fixer scope and constraints
Any file may be touched -- a secret could be anywhere -- but the diff must be
as narrow as possible: remove or replace the specific secret with an
environment-variable read, never a broad refactor riding along with it.

## What a correct fix looks like
- Removes the literal secret value from source entirely.
- Replaces it with a read from environment/configuration, matching however
  this repo already reads other configuration (do not invent a new config
  system for one fix).
- Does not print, log, or echo the removed secret value anywhere in the diff,
  the commit message, or the PR body.
"""

PERFORMANCE_RULES = """\
Category: performance

## Detector
A bundle-size budget check over shipped `.js` files (a fixed byte budget, not
a profiler or a runtime timing measurement). A candidate issue is total
shipped JS exceeding the configured budget. Evidence attached: the per-file
size breakdown and the exact number of bytes over budget.

## What counts as a performance finding here
- Total shipped JavaScript crosses the configured byte budget -- this is a
  SIZE regression specifically, not a runtime-speed regression (this detector
  cannot observe CPU time, render time, or network latency; do not claim it
  found one of those).
- The size growth traces to a specific file/change, not general drift with no
  identifiable cause.

## Severity guidance
- High: the budget is exceeded by a large margin (roughly 2x the configured
  budget or more) or by content that clearly doesn't need to ship (e.g.
  debug-only code, an accidentally-duplicated blob).
- Medium: modestly over budget from a real feature addition that could
  plausibly be trimmed.
- Low: barely over budget, close enough that the budget itself may be the
  thing miscalibrated rather than the code being wrong.

## Skeptic's toolkit -- common false positives in THIS shape of finding
- The size increase is a genuinely new feature's necessary code, not bloat --
  check whether the added bytes correspond to new functionality actually
  reachable from the UI, not dead weight.
- The configured budget itself is stale/miscalibrated for what this repo now
  legitimately needs to ship -- argue for a budget change over a code change
  when that's honestly the better fix (the Arbiter can weigh this; state it
  explicitly rather than forcing a code diff that doesn't belong).
- Whitespace/comment bytes inflate the raw count without affecting what
  actually executes -- note if the detector counts uncompressed source rather
  than a minified/gzipped size, since that changes how alarming a given
  overage really is.

## Corroborator's toolkit -- what real evidence looks like
- The per-file breakdown names a specific file whose size jumped between
  commits, not a diffuse increase across many files.
- The added content is inspectable and is clearly NOT load-bearing for any
  reachable feature (e.g. a duplicated library, dead code, an accidentally
  committed data blob).

## Fixer scope and constraints
Backend or frontend, whichever file actually grew -- follow the evidence, not
a fixed assumption about which side performance problems live on.

## What a correct fix looks like
- Removes or trims the specific bytes identified in the evidence (dead code,
  duplication, an unnecessarily large embedded asset) rather than shrinking
  something unrelated to make the total number work.
- Brings the total back under budget without removing reachable
  functionality -- a fix that breaks a feature to hit a byte target is worse
  than the regression it was meant to fix.
"""

DOCUMENTATION_RULES = """\
Category: documentation

## Detector
A doc-vs-code drift check: every `functionName(` reference inside README.md's
inline code spans must name a real symbol that actually exists in the source
(cross-checked against the same symbol table the dependency graph uses). A
candidate issue is a referenced function that doesn't exist under that exact
name anymore. Evidence attached: the drifted reference(s) as written in the
README, and the real symbol names actually found in the source.

## What counts as a documentation finding here
- README.md names a function that was renamed, removed, or was always a typo
  -- readers following the README would call something that doesn't exist.
This category is narrow ON PURPOSE: it checks reference EXISTENCE, not prose
accuracy, not whether examples still produce the described output, and not
staleness of unrelated documentation content.

## Severity guidance
- Medium (default): any drifted reference is equally worth fixing -- a reader
  following stale docs hits a real error regardless of which function it is.
- This category runs the lowest thresholds of the five (assurance 65,
  resolution 75) precisely because the check is narrow and mechanical: a
  reference either exists or it doesn't, so a lower bar for raising it is
  appropriate, and a straightforward drift needs no exotic evidence to fix.

## Skeptic's toolkit -- common false positives in THIS shape of finding
- The reference documents a planned, not-yet-implemented function
  deliberately (check for surrounding words like "will", "planned", "coming
  soon" near the reference before treating it as drift).
- The symbol exists but under a different KIND (e.g. it's a class or a
  constant, not a function) that the extractor's regex-based symbol table
  doesn't currently classify the same way -- verify by reading the actual
  source before agreeing it's missing.
- The reference is inside a code BLOCK showing example output or a
  third-party API call, not describing a symbol this repo defines at all.

## Corroborator's toolkit -- what real evidence looks like
- The named function genuinely does not appear anywhere in the indexed
  source under that name, and no clear rename target is obvious from
  context (if a rename target IS obvious, name it -- that sharpens the fix).
- The reference sits in a section presented as current, working
  documentation (a usage example, an API list), not a changelog entry
  describing past or future state.

## Fixer scope and constraints
Documentation files only (README.md and other `.md` files) -- this category
never touches application code. If the "right" fix looks like renaming code
to MATCH the docs rather than fixing the docs to match the code, that is out
of this category's scope; raise it as a separate finding instead.

## What a correct fix looks like
- Updates the drifted reference to name the real, current symbol (or removes
  it if no equivalent exists), preserving the surrounding sentence's meaning.
- Touches only the drifted reference(s) identified in the evidence -- not a
  broader rewrite of the README's tone or structure.
"""

ACCESSIBILITY_RULES = """\
Category: accessibility

## Detector
A dedicated axe-core check (tests/accessibility.spec.ts, tagged @a11y),
axe-core itself injected from a CDN at test time rather than added as an npm
dependency. A candidate issue is any axe violation whose impact is `serious`
or `critical` (not `minor`/`moderate` -- those are real but not worth an
automatic issue on their own; a human auditing the repo can still see them in
the full axe report attached as evidence). Evidence attached: the violated
rule id, its WCAG tag(s), the human-readable help text, and the specific
DOM node(s) that failed, with the failure's concrete measured values (e.g. a
measured contrast ratio against the required one) where axe reports them.

## What counts as an accessibility finding here
- A `serious`/`critical` WCAG 2A or 2AA violation axe can detect
  mechanically: insufficient color contrast, an interactive control with no
  accessible name (no `<label>`, `aria-label`, or `aria-labelledby` --
  placeholder text alone does NOT count as a label), missing alt text on a
  meaningful image, invalid ARIA usage, a heading structure that skips
  levels.
This category is what a machine can verify (axe's own rule engine), not a
full manual accessibility audit -- screen-reader UX judgment, keyboard-trap
scenarios axe can't simulate, and cognitive-load concerns are out of scope
for this detector; name them in the verdict as "worth a manual pass" rather
than implying automated coverage that doesn't exist.

## Severity guidance
- High: `critical` impact per axe, or a `serious` violation on the page's
  primary interactive control (the one most users touch first).
- Medium: `serious` impact on a secondary control.
- Low: this detector does not raise on `minor`/`moderate` impact at all --
  by design, to keep the signal-to-noise ratio usable; do not upgrade one to
  a finding on your own judgment, note it as an aside instead.

## Skeptic's toolkit -- common false positives in THIS shape of finding
- A contrast ratio measured against a background that isn't actually what
  renders in practice (e.g. axe measured against a CSS variable that's
  overridden by a theme/dark-mode class not active on this page) -- verify
  the CSS that ACTUALLY applies, not just what axe's static computation used.
- A false "missing label" on a control that has an accessible name through a
  mechanism axe's ruleset under-recognizes (e.g. `aria-labelledby` pointing
  at visually-hidden text) -- read the actual markup, not just the verdict.
- A third-party embed (an iframe from another origin) axe can't fully
  introspect and flags conservatively -- if the failing node isn't this
  repo's own markup, that's a different kind of finding entirely.

## Corroborator's toolkit -- what real evidence looks like
- The measured value axe reports (contrast ratio, missing attribute) is
  independently checkable by reading the relevant HTML/CSS directly -- do
  that read and cite what you found, don't just repeat axe's own message.
- The violated control is one a real user path actually reaches (not dead
  markup, not something hidden behind a flag that's always off).

## Fixer scope and constraints
Frontend files only (markup and styles) -- this category never touches
backend logic.

## What a correct fix looks like
- Addresses the ROOT cause axe named: adds the missing label/name, adjusts
  the specific color pair to clear the required contrast ratio, fixes the
  specific invalid ARIA usage -- not a global restyle riding along with it.
- Re-running the SAME axe check (tests/accessibility.spec.ts, unmodified)
  reports zero serious/critical violations for the fixed control.
- Preserves the page's visual intent as much as possible -- if a color must
  change to clear a contrast requirement, pick the smallest adjustment that
  clears it, not an unrelated palette change.
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
    "accessibility": CategoryConfig(
        key="accessibility",
        label="Accessibility issues",
        detector_tools="axe-core (WCAG 2A/2AA, serious+ impact)",
        scope_glob=re.compile(r"^(playwright\.config\.ts|tests/|backend/)"),
        assurance_threshold=75,
        resolution_threshold=80,
        rules=ACCESSIBILITY_RULES,
        entry_files=("index.html", "style.css"),
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
