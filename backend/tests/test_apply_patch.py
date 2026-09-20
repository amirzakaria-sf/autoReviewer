"""The write primitives' refusal contract.

Every case here is a refusal the model has to be able to act on, so each
assertion checks the SENTENCE as well as the fact that nothing was written --
"REFUSED" with no explanation leaves the loop to guess, and guessing is what
the tripwire then counts as a repeat.
"""

from __future__ import annotations

import pytest

from app.sandbox.apply_patch import apply_patch, write_file


@pytest.fixture
def worktree(tmp_path):
    (tmp_path / "app.js").write_text("function a() { return 1; }\nfunction b() { return 1; }\n")
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "server.js").write_text("listen(3000)\n")
    return tmp_path


def test_a_unique_anchor_is_replaced_and_nothing_else_moves(worktree):
    result = apply_patch(worktree, "ui", "app.js", "function a() { return 1; }", "function a() { return 2; }")
    assert result.startswith("patched app.js")
    assert (worktree / "app.js").read_text() == "function a() { return 2; }\nfunction b() { return 1; }\n"


def test_replace_all_changes_every_occurrence(worktree):
    result = apply_patch(worktree, "ui", "app.js", "return 1;", "return 2;", replace_all=True)
    assert "2 replacement(s)" in result
    assert (worktree / "app.js").read_text().count("return 2;") == 2


def test_an_ambiguous_anchor_is_refused_with_advice_and_writes_nothing(worktree):
    before = (worktree / "app.js").read_text()
    result = apply_patch(worktree, "ui", "app.js", "return 1;", "return 2;")
    assert result.startswith("REFUSED")
    assert "appears 2 times" in result and "replace_all=true" in result
    assert (worktree / "app.js").read_text() == before


def test_an_anchor_that_does_not_match_is_refused_with_the_byte_for_byte_rule(worktree):
    result = apply_patch(worktree, "ui", "app.js", "function a() {return 1;}", "x")
    assert result.startswith("REFUSED")
    assert "byte for byte" in result


def test_an_empty_anchor_points_at_write_file(worktree):
    result = apply_patch(worktree, "ui", "app.js", "", "anything")
    assert "write_file" in result


def test_an_identical_replacement_is_refused_rather_than_counted_as_progress(worktree):
    result = apply_patch(worktree, "ui", "app.js", "return 1;", "return 1;", replace_all=True)
    assert result.startswith("REFUSED") and "nothing to do" in result


def test_a_missing_file_points_at_write_file_instead_of_creating_it(worktree):
    result = apply_patch(worktree, "ui", "new.js", "a", "b")
    assert "does not exist" in result and "write_file" in result
    assert not (worktree / "new.js").exists()


def test_a_path_escaping_the_worktree_is_refused(worktree):
    (worktree.parent / "outside.js").write_text("secret\n")
    result = apply_patch(worktree, "ui", "../outside.js", "secret", "leaked")
    assert result.startswith("REFUSED") and "outside the worktree" in result
    assert (worktree.parent / "outside.js").read_text() == "secret\n"


def test_write_file_also_refuses_to_escape_the_worktree(worktree):
    result = write_file(worktree, "ui", "../escaped.js", "x")
    assert result.startswith("REFUSED")
    assert not (worktree.parent / "escaped.js").exists()


def test_the_write_scope_is_enforced_by_the_primitive_itself(worktree):
    """`scope_glob` is an EXCLUSION set: the ui category excludes backend/, so a
    UI fix cannot reach a server file even though the path is inside the tree."""
    before = (worktree / "backend" / "server.js").read_text()
    result = apply_patch(worktree, "ui", "backend/server.js", "listen(3000)", "listen(4000)")
    assert result.startswith("REFUSED") and "write scope" in result
    assert (worktree / "backend" / "server.js").read_text() == before


def test_write_file_is_scoped_by_the_same_rule(worktree):
    result = write_file(worktree, "ui", "backend/server.js", "anything")
    assert result.startswith("REFUSED") and "write scope" in result


def test_write_file_creates_a_new_file_inside_the_scope(worktree):
    result = write_file(worktree, "ui", "helper.js", "export const x = 1;\n")
    assert result == "wrote helper.js"
    assert (worktree / "helper.js").read_text() == "export const x = 1;\n"
