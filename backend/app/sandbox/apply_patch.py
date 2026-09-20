"""The worktree write primitives the patch worker is given.

Pure filesystem. Nothing here starts a container, and nothing here belongs in a
router -- these run in the worker process, inside a worktree the Fix Council
already owns.

`apply_patch` is the preferred edit and `write_file` is the fallback, which is
the opposite of how this project started. Full-file rewriting is the most
expensive habit an agent can have: restating a 2,000-line file to change three
lines costs the output tokens for all 2,000, and -- the part that actually
hurts here -- it lets the model silently drop or reword code it was asked to
reproduce verbatim. A category detector only checks the behaviour it was
written to check, so a rewrite can pass the gate and still have broken a
sibling function nobody was looking at. An anchored replace can only change the
bytes it names.

**Exact match, never fuzzy.** A patcher that "mostly" finds its anchor will
eventually apply an edit somewhere subtly wrong, and neither the model nor the
gates can see that it happened. Every failure here is loud and tells the model
how to fix it, which is something it can act on within the same loop.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from app.categories import scope_excludes


def _resolve(worktree: Path, rel_path: str) -> Path | None:
    """The path, or None if it escapes the worktree. `..` in a model-supplied
    path is the whole reason this exists; resolve() is what makes the check
    real rather than a substring test that `a/../../b` walks straight past."""
    try:
        candidate = (worktree / rel_path).resolve()
        root = worktree.resolve()
    except OSError:
        return None
    if candidate != root and root not in candidate.parents:
        return None
    return candidate


def _atomic_write(target: Path, content: str) -> str | None:
    """Write via a temp file in the SAME directory, then rename. A crash
    mid-write otherwise leaves a half-file, which then fails the detector for a
    reason the model can neither see nor fix. Returns an error sentence, or None."""
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=str(target.parent), delete=False) as handle:
            handle.write(content)
            temporary = Path(handle.name)
        temporary.replace(target)
    except OSError as error:
        return f"ERROR: could not write {target.name}: {error}"
    return None


def apply_patch(
    worktree: Path,
    category: str,
    rel_path: str,
    old_string: str,
    new_string: str,
    *,
    replace_all: bool = False,
) -> str:
    """Replace an exact span of an existing file. Returns a one-line result --
    never raises for anything the model could have caused, because every one of
    these failures is something it can correct on the next tick."""
    if scope_excludes(category, rel_path):
        return f"REFUSED: {rel_path} is outside the {category} category's write scope."

    target = _resolve(worktree, rel_path)
    if target is None:
        return f"REFUSED: {rel_path} is outside the worktree."
    if not target.is_file():
        return f"REFUSED: {rel_path} does not exist. Use write_file to create a new file."
    if not old_string:
        return "REFUSED: old_string is empty. Use write_file to replace a file's entire contents."
    if old_string == new_string:
        return "REFUSED: old_string and new_string are identical; nothing to do."

    try:
        original = target.read_text(encoding="utf-8", errors="replace")
    except OSError as error:
        return f"ERROR: could not read {rel_path}: {error}"

    occurrences = original.count(old_string)
    if occurrences == 0:
        return (
            f"REFUSED: old_string was not found in {rel_path}. It must match the file byte for "
            "byte, including indentation. Re-read the exact lines you intend to replace and try "
            "again with the text as it actually appears."
        )
    if occurrences > 1 and not replace_all:
        return (
            f"REFUSED: old_string appears {occurrences} times in {rel_path}, so it is ambiguous. "
            "Include surrounding lines to make the anchor unique, or pass replace_all=true if "
            "every occurrence should change."
        )

    updated = original.replace(old_string, new_string) if replace_all else original.replace(old_string, new_string, 1)
    error = _atomic_write(target, updated)
    if error:
        return error
    return f"patched {rel_path} ({occurrences if replace_all else 1} replacement(s))"


def write_file(worktree: Path, category: str, rel_path: str, content: str) -> str:
    """Create a file, or replace one outright. Kept because `apply_patch`
    genuinely cannot do either: there is no anchor in a file that does not
    exist yet."""
    if scope_excludes(category, rel_path):
        return f"REFUSED: {rel_path} is outside the {category} category's write scope."

    target = _resolve(worktree, rel_path)
    if target is None:
        return f"REFUSED: {rel_path} is outside the worktree."

    error = _atomic_write(target, content)
    if error:
        return error
    return f"wrote {rel_path}"
