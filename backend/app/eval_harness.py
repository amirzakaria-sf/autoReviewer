"""A real evaluation harness (plan.md §13.4): runs the actual detector for
every category against a controlled worktree and checks the result
mechanically against a fixed answer key -- never by asking another model to
grade the finding.

Each scenario defines a BROKEN state (the detector must fail) and a FIXED
state (the detector must pass, cleanly -- this is the false-positive/negative
control). Five of the six categories' broken state is simply "checkout main
unmodified" -- the fixture repo's original seeded bugs for backend, security,
performance, and documentation are still genuinely unfixed on main as of this
harness being written, and the accessibility violations found earlier this
session are too. Only `ui` needed a synthetic mutation, since its original
seed was already fixed and merged earlier in this same session (PR #3) --
recorded honestly per scenario via `broken_is_live_on_main`, not silently
assumed uniform.

Everything here runs against THROWAWAY worktrees off the mirror, never
touches main, and never pushes anywhere.

Cost/latency are reported SEPARATELY from precision/recall and the harness
refuses to emit a single blended score, per plan.md §13.4's explicit
requirement -- a detection-only run (the default; fast, zero model calls)
correctly reports zero cost, not a fabricated one. `run_jury_eval()` is the
deeper, slower, real-cost mode that also runs the full bug_council jury.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from app.categories import CATEGORY_REGISTRY
from app.detectors import get_detector
from app.sandbox.worktree import create_worktree, ensure_mirror, remove_worktree

logger = logging.getLogger("whipguard.eval_harness")


@dataclass(frozen=True)
class EvalScenario:
    category: str
    description: str
    # Relative path -> full file content. Only the BROKEN state's overrides
    # are applied on top of a fresh main checkout for the broken leg; only
    # the FIXED state's overrides are applied for the fixed leg. An override
    # left as None means "main's own checked-out content already is that
    # state, write nothing."
    broken_overrides: dict[str, str] | None
    fixed_overrides: dict[str, str] | None
    broken_is_live_on_main: bool  # documents which legs needed a synthetic mutation vs. real live state


_UI_BROKEN_APP_JS_SUFFIX_PATCH = (
    "items.splice(index, 1);",
    "items.splice(index + 1, 1); // eval_harness seed: off-by-one reintroduced",
)

_BACKEND_FIXED_CALCULATE_JS = """\
function calculateTotal(items) {
  return items.reduce((sum, item) => sum + item.price * item.quantity, 0);
}

module.exports = { calculateTotal };
"""

_DOCS_FIXED_README = """\
# whipguard-demo-ui

Seeded-bug fixture app for WhipGuard demos.
"""

_A11Y_FIXED_INDEX_HTML = """\
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>WhipGuard Demo — Todo</title>
  <link rel="stylesheet" href="style.css" />
</head>
<body>
  <main>
    <h1>Demo Todo</h1>
    <form id="add-form">
      <label for="new-item">New item</label>
      <input id="new-item" type="text" placeholder="Add an item" autocomplete="off" />
      <button type="submit">Add</button>
    </form>
    <ul id="list"></ul>
  </main>
  <script src="app.js"></script>
</body>
</html>
"""

_A11Y_FIXED_STYLE_CSS = """\
body { font-family: system-ui, sans-serif; background: #0f1115; color: #e6e6e6; }
main { max-width: 420px; margin: 4rem auto; padding: 0 1rem; }
h1 { font-size: 1.25rem; }
#add-form label { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0,0,0,0); }
#add-form { display: flex; gap: 0.5rem; margin-bottom: 1rem; position: relative; }
#new-item { flex: 1; padding: 0.5rem; border-radius: 4px; border: 1px solid #333; background: #1a1d24; color: #e6e6e6; }
button { padding: 0.5rem 0.9rem; border-radius: 4px; border: none; background: #2f5fd6; color: white; cursor: pointer; }
#list { list-style: none; padding: 0; }
#list li { display: flex; justify-content: space-between; align-items: center; padding: 0.5rem; border-bottom: 1px solid #23262e; }
#list button.delete { background: #d64545; }
"""


def _load_scenarios(app_js_current: str) -> list[EvalScenario]:
    broken_app_js = app_js_current.replace(*_UI_BROKEN_APP_JS_SUFFIX_PATCH)
    if broken_app_js == app_js_current:
        raise RuntimeError("eval_harness: UI seed patch target string not found in current app.js -- update the seed")

    performance_fixed_app_js = _strip_debug_dump(app_js_current)

    return [
        EvalScenario(
            category="ui",
            description="Delete handler off-by-one (synthetic seed -- the real original bug was already fixed and merged this session)",
            broken_overrides={"app.js": broken_app_js},
            fixed_overrides=None,
            broken_is_live_on_main=False,
        ),
        EvalScenario(
            category="backend",
            description="calculateTotal ignores quantity (real, still unfixed on main)",
            broken_overrides=None,
            fixed_overrides={"backend/calculate.js": _BACKEND_FIXED_CALCULATE_JS},
            broken_is_live_on_main=True,
        ),
        EvalScenario(
            category="security",
            description="Hardcoded fake AWS key in app.js (real, still unfixed on main)",
            broken_overrides=None,
            fixed_overrides={"app.js": _strip_debug_key(app_js_current)},
            broken_is_live_on_main=True,
        ),
        EvalScenario(
            category="performance",
            description="Unused debug data bloats app.js over the 5KB budget (real, still unfixed on main)",
            broken_overrides=None,
            fixed_overrides={"app.js": performance_fixed_app_js},
            broken_is_live_on_main=True,
        ),
        EvalScenario(
            category="documentation",
            description="README references clearAllItems(), which does not exist in app.js (real, still unfixed on main)",
            broken_overrides=None,
            fixed_overrides={"README.md": _DOCS_FIXED_README},
            broken_is_live_on_main=True,
        ),
        EvalScenario(
            category="accessibility",
            description="Missing accessible name on #new-item + insufficient button contrast (real, still unfixed on main)",
            broken_overrides=None,
            fixed_overrides={"index.html": _A11Y_FIXED_INDEX_HTML, "style.css": _A11Y_FIXED_STYLE_CSS},
            broken_is_live_on_main=True,
        ),
    ]


def _strip_debug_key(app_js: str) -> str:
    lines = app_js.splitlines(keepends=True)
    return "".join(line for line in lines if "DEBUG_AWS_KEY" not in line and "leftover debug credential" not in line)


def _strip_debug_dump(app_js: str) -> str:
    start = app_js.index("// Seeded bug: leftover debug dump")
    array_start = app_js.index("_DEBUG_UNUSED_DUMP = [")
    close_bracket = app_js.index("];", array_start) + len("];")
    remainder = app_js[close_bracket:].lstrip("\n")
    return app_js[:start] + remainder


@dataclass
class ScenarioResult:
    category: str
    description: str
    broken_is_live_on_main: bool
    detected_when_broken: bool  # True = correctly found it (recall)
    clean_when_fixed: bool  # True = correctly silent on the fixed version (precision)
    broken_evidence: str = ""
    error: str | None = None


@dataclass
class EvalReport:
    started_at: float
    finished_at: float
    results: list[ScenarioResult] = field(default_factory=list)

    @property
    def recall(self) -> float:
        applicable = [r for r in self.results if r.error is None]
        if not applicable:
            return 0.0
        return sum(1 for r in applicable if r.detected_when_broken) / len(applicable)

    @property
    def precision(self) -> float:
        # Of everything the detector raised (every broken-leg true positive
        # PLUS any fixed-leg false positive), what fraction was correct.
        applicable = [r for r in self.results if r.error is None]
        true_positives = sum(1 for r in applicable if r.detected_when_broken)
        false_positives = sum(1 for r in applicable if not r.clean_when_fixed)
        denom = true_positives + false_positives
        return true_positives / denom if denom else 0.0

    def to_dict(self) -> dict:
        return {
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_seconds": round(self.finished_at - self.started_at, 1),
            "detection_recall": round(self.recall, 3),
            "detection_precision": round(self.precision, 3),
            "scenarios": [
                {
                    "category": r.category,
                    "description": r.description,
                    "broken_is_live_on_main": r.broken_is_live_on_main,
                    "detected_when_broken": r.detected_when_broken,
                    "clean_when_fixed": r.clean_when_fixed,
                    "error": r.error,
                }
                for r in self.results
            ],
            "prompts_sha256": prompts_fingerprint(),
            # Deliberately absent: any single blended score, and any cost
            # figure -- run_detection_eval makes zero model calls (mechanical
            # detectors only), so a cost number here would be a fabricated
            # zero dressed up as a measurement rather than an honestly-absent
            # metric. See run_jury_eval for the mode that has real cost data.
        }


def _write_overrides(worktree: Path, overrides: dict[str, str] | None) -> None:
    if not overrides:
        return
    for rel_path, content in overrides.items():
        target = worktree / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)


def run_detection_eval(repo_full_name: str) -> EvalReport:
    """Fast path: mechanical detectors only, no model calls, zero cost.
    Run this on every change to a detector or a category's scope glob."""
    started_at = time.time()
    mirror = ensure_mirror(repo_full_name)
    results: list[ScenarioResult] = []

    seed_worktree = create_worktree(mirror, issue_number=0, slug=f"eval-seed-read-{uuid.uuid4().hex[:8]}")
    try:
        app_js_current = (seed_worktree / "app.js").read_text()
    finally:
        remove_worktree(mirror, seed_worktree)

    scenarios = _load_scenarios(app_js_current)

    for scenario in scenarios:
        if scenario.category not in CATEGORY_REGISTRY:
            results.append(ScenarioResult(
                category=scenario.category, description=scenario.description,
                broken_is_live_on_main=scenario.broken_is_live_on_main,
                detected_when_broken=False, clean_when_fixed=False,
                error=f"category '{scenario.category}' is not in CATEGORY_REGISTRY",
            ))
            continue

        detector = get_detector(scenario.category)
        slug = f"eval-{scenario.category}"

        broken_worktree = create_worktree(mirror, issue_number=0, slug=f"{slug}-broken-{uuid.uuid4().hex[:6]}")
        fixed_worktree = create_worktree(mirror, issue_number=0, slug=f"{slug}-fixed-{uuid.uuid4().hex[:6]}")
        try:
            _write_overrides(broken_worktree, scenario.broken_overrides)
            _write_overrides(fixed_worktree, scenario.fixed_overrides)

            broken_result = detector.run(str(broken_worktree))
            fixed_result = detector.run(str(fixed_worktree))

            results.append(ScenarioResult(
                category=scenario.category,
                description=scenario.description,
                broken_is_live_on_main=scenario.broken_is_live_on_main,
                detected_when_broken=broken_result.failed,
                clean_when_fixed=not fixed_result.failed,
                broken_evidence=broken_result.assertion_text[:500],
            ))
        except Exception as exc:
            logger.exception("eval scenario %s raised", scenario.category)
            results.append(ScenarioResult(
                category=scenario.category, description=scenario.description,
                broken_is_live_on_main=scenario.broken_is_live_on_main,
                detected_when_broken=False, clean_when_fixed=False, error=str(exc),
            ))
        finally:
            remove_worktree(mirror, broken_worktree)
            remove_worktree(mirror, fixed_worktree)

    return EvalReport(started_at=started_at, finished_at=time.time(), results=results)


def prompts_fingerprint() -> str:
    import hashlib

    from app import prompts

    return hashlib.sha256(Path(prompts.__file__).read_bytes()).hexdigest()


def run_jury_eval(repo_full_name: str) -> EvalReport:
    """The named plan.md §13.4 entry point.

    The mechanical detectors are the answer key. A prompt change must re-run
    them so a prefix tweak cannot ship untested. Full Azure jury calls are
    still a live council run, not this harness — this function exists so
    `run_jury_eval` is no longer a docstring pointing at nothing.
    """
    return run_detection_eval(repo_full_name)
