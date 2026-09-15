"""Unit tests for eval_harness's file-transform helpers against the actual
current app.js content (fixture below is the real file as of this test
being written -- if the fixture repo's app.js changes, this test's own
failure is the signal to update it, which is the point: these helpers must
keep matching the real file, not a stale assumption of it)."""

from __future__ import annotations

from app.eval_harness import _load_scenarios, _strip_debug_dump, _strip_debug_key

_REAL_APP_JS = '''\
const items = [];
const list = document.getElementById("list");
const form = document.getElementById("add-form");
const input = document.getElementById("new-item");

function render() {
  list.innerHTML = "";
  items.forEach((text, index) => {
    const li = document.createElement("li");
    li.dataset.index = String(index);
    const span = document.createElement("span");
    span.textContent = text;
    const del = document.createElement("button");
    del.className = "delete";
    del.textContent = "Delete";
    del.addEventListener("click", () => deleteItem(index));
    li.append(span, del);
    list.append(li);
  });
}

function deleteItem(index) {
  items.splice(index, 1);
  render();
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  const value = input.value.trim();
  if (!value) return;
  items.push(value);
  input.value = "";
  render();
});

render();

// Seeded bug: leftover debug credential, never meant to ship.
const DEBUG_AWS_KEY = "AKIAQ7X3K9M2P5R8T1WZ";

// Seeded bug: leftover debug dump, unused, bloats the shipped bundle.
const _DEBUG_UNUSED_DUMP = [
  "debug-row-0-unused-placeholder-data-padding-value",
  "debug-row-1-unused-placeholder-data-padding-value",
];
'''


def test_load_scenarios_covers_all_six_categories():
    scenarios = _load_scenarios(_REAL_APP_JS)
    categories = {s.category for s in scenarios}
    assert categories == {"ui", "backend", "security", "performance", "documentation", "accessibility"}


def test_ui_seed_patch_reintroduces_the_off_by_one():
    scenarios = _load_scenarios(_REAL_APP_JS)
    ui = next(s for s in scenarios if s.category == "ui")
    seeded = ui.broken_overrides["app.js"]
    assert "items.splice(index, 1);" not in seeded
    assert "items.splice(index + 1, 1);" in seeded


def test_strip_debug_key_removes_the_secret_and_nothing_else():
    stripped = _strip_debug_key(_REAL_APP_JS)
    assert "DEBUG_AWS_KEY" not in stripped
    assert "AKIAQ7X3K9M2P5R8T1WZ" not in stripped
    # The performance-category bug is untouched by the security fix.
    assert "_DEBUG_UNUSED_DUMP" in stripped
    assert "function deleteItem" in stripped


def test_strip_debug_dump_removes_the_array_and_nothing_else():
    stripped = _strip_debug_dump(_REAL_APP_JS)
    assert "_DEBUG_UNUSED_DUMP" not in stripped
    assert "debug-row-0" not in stripped
    # The security-category bug is untouched by the performance fix.
    assert "DEBUG_AWS_KEY" in stripped
    assert "function deleteItem" in stripped
    assert len(stripped) < len(_REAL_APP_JS)


def test_load_scenarios_raises_clearly_if_seed_target_string_is_gone():
    mutated = _REAL_APP_JS.replace("items.splice(index, 1);", "items.pop();")
    try:
        _load_scenarios(mutated)
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert "seed patch target" in str(exc)
