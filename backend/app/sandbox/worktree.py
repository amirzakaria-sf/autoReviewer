"""Per-repo bare mirror + per-fix worktrees (plan.md §4).

{org}/{repo}/mirror/            bare git mirror, fetched on webhook/poll, never worked in
{org}/{repo}/counsel/           a persistent checkout of the default branch, for reads
{org}/{repo}/fixes/{n}-{slug}/  a git WORKTREE off mirror/, one per active fix

The `{org}` level is the same shape the sibling `opencode` deployment uses
for its managed projects (`<owner>/<project>`): clones are grouped by the
tenant that owns them, not dumped flat into one directory. It matters for
more than tidiness -- "how much disk is this org using" and "delete
everything belonging to this org" are both one path operation rather than a
database query joined against a directory listing.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

from app.config import settings

logger = logging.getLogger("whipguard.worktree")

WORKSPACE_ROOT = Path(settings.workspace_root)

# A repo that is not in an organization yet still has to land SOMEWHERE
# deterministic. Leading underscore so it can never collide with a real org
# slug (slugs are generated from names and cannot start with one).
_NO_ORG_DIR = "_unassigned"

# Org membership changes about never, and this is resolved on every mirror
# fetch, every worktree creation and every Counsel tool call -- so it is
# cached rather than queried each time. Short TTL rather than permanent: a
# repo moved into an org during a run should follow within a minute, and
# _relocate_if_needed below makes that transition safe when it does.
_ORG_CACHE_TTL_SECONDS = 60.0
_org_cache: dict[str, tuple[str, float]] = {}
_org_cache_lock = threading.Lock()

# Matches docker_runner.py's own SANDBOX_UID/GID -- the sandbox container
# runs as this UID (the HOST user's, not root), but this backend process
# itself runs as root (no USER in the Dockerfile), so every worktree it
# creates is root-owned. A root-owned directory blocks the non-root sandbox
# from writing into it at all -- `npm install` inside failed completely
# silent (Docker returned an exit code with empty stdout/stderr, no
# "permission denied" visible anywhere) until this was tracked down by
# actually running the eval harness and comparing a raw `ls -la` against
# the sandbox's own `id`, not by assuming detection working once meant
# every worktree path was fine.
_SANDBOX_UID = int(os.environ.get("SANDBOX_UID", "1000"))
_SANDBOX_GID = int(os.environ.get("SANDBOX_GID", "1000"))


def repo_slug(github_full_name: str) -> str:
    return github_full_name.replace("/", "__")


def _org_dir(github_full_name: str) -> str:
    """The organization slug that owns this repo, as a directory name.

    Falls back to `_unassigned` when the repo has no org, when the column is
    absent (an older schema), or when the database simply cannot be reached.
    A failed lookup must never raise here: every path in this module is on
    the critical path of a council run, and a transient database hiccup
    turning into a crashed detection run would be a far worse outcome than a
    clone landing in the fallback directory for one run.
    """
    now = time.monotonic()
    with _org_cache_lock:
        cached = _org_cache.get(github_full_name)
        if cached is not None and cached[1] > now:
            return cached[0]

    slug = _NO_ORG_DIR
    try:
        from app import sync_db

        with sync_db.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT o.slug
                FROM repos r
                JOIN organizations o ON o.id = r.org_id
                WHERE r.github_full_name = %s
                """,
                (github_full_name,),
            )
            row = cur.fetchone()
        if row and row[0]:
            slug = str(row[0]).replace("/", "_").strip() or _NO_ORG_DIR
    except Exception:  # noqa: BLE001 - see docstring; a miss is survivable
        logger.warning("org lookup failed for %s; using %s", github_full_name, _NO_ORG_DIR)
        # Deliberately NOT cached: a database that is briefly down must not
        # pin this repo to the fallback directory for the next minute.
        return _NO_ORG_DIR

    with _org_cache_lock:
        _org_cache[github_full_name] = (slug, now + _ORG_CACHE_TTL_SECONDS)
    return slug


def _is_dir(path: Path) -> bool:
    """`Path.is_dir()` that cannot raise.

    Python 3.12 stopped swallowing every OSError here: a directory the
    process may not traverse now raises PermissionError out of `is_dir()`
    rather than answering False. The workspace lives on the data disk, whose
    parent is root-only, so a non-root caller (the local venv, the test
    suite) hits exactly that -- and a path probe that can raise turns an
    inaccessible directory into a crashed council run.
    """
    try:
        return path.is_dir()
    except OSError:
        return False


def _legacy_candidates(github_full_name: str, current: Path) -> list[Path]:
    """Where this repo's tree might be sitting from an earlier layout.

    Two cases, both real: the pre-org FLAT layout (`{root}/{slug}`), and a
    repo that has since moved between organizations (or out of
    `_unassigned` into its first one).
    """
    slug = repo_slug(github_full_name)
    candidates = [WORKSPACE_ROOT / slug]
    if _is_dir(WORKSPACE_ROOT):
        try:
            candidates.extend(
                child / slug for child in WORKSPACE_ROOT.iterdir() if child.is_dir()
            )
        except OSError:
            pass
    return [path for path in candidates if path != current and _is_dir(path)]


def _relocate(source: Path, destination: Path) -> bool:
    """Move an existing tree to its new org directory, in place.

    Re-cloning instead would be simpler, but it throws away every open fix
    worktree along with the mirror -- so a repo being assigned to an org
    would silently abandon whatever fixes were mid-flight. `git worktree
    repair` exists exactly for this: worktree links are recorded as ABSOLUTE
    paths in both directions (mirror/worktrees/*/gitdir and each worktree's
    own .git file), so a moved tree is broken until both ends are rewritten.
    """
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        # The org directory is created by this process, which runs as root --
        # same root-owned-directory trap the worktree chowns below already
        # exist for. Harmless today (nothing writes at this level) but it
        # costs one call to keep the whole tree consistently owned.
        try:
            os.chown(destination.parent, _SANDBOX_UID, _SANDBOX_GID)
        except OSError:
            pass
        shutil.move(str(source), str(destination))
    except OSError as error:
        logger.warning("could not relocate %s -> %s: %s", source, destination, error)
        return False

    mirror = destination / "mirror"
    if mirror.is_dir():
        # `counsel` sits directly under the repo root; fix worktrees sit one
        # level deeper under `fixes/`. repair needs every one of them named
        # explicitly -- it rewrites the link from the worktree's side, and
        # git cannot discover worktrees whose recorded paths are stale.
        worktrees: list[str] = []
        for parent in (destination, destination / "fixes"):
            if not parent.is_dir():
                continue
            for path in parent.iterdir():
                if path.is_dir() and (path / ".git").exists():
                    worktrees.append(str(path))
        subprocess.run(
            ["git", "--git-dir", str(mirror), "worktree", "repair", *worktrees],
            check=False, capture_output=True, text=True, timeout=120,
        )
        _repair_worktree_links(mirror, destination)
    logger.info("relocated workspace %s -> %s", source, destination)
    return True


def _repair_worktree_links(mirror: Path, destination: Path) -> None:
    """Rewrite BOTH halves of every worktree link under `destination`.

    A linked worktree is held together by two absolute paths pointing at each
    other: `mirror/worktrees/<name>/gitdir` names the worktree's `.git` file,
    and that `.git` file names the `mirror/worktrees/<name>` directory back.
    Relocating the repo root invalidates both at once.

    `git worktree repair` is run first and handles the ordinary cases, but it
    cannot recover a link whose recorded mirror path no longer exists on this
    filesystem at all -- which is the state left behind by anything created
    under a different WORKSPACE_ROOT (a venv run writing host paths that the
    container never had). Those entries survive as worktrees git lists but
    cannot open.

    This pass does not need either recorded path to be valid, because the
    layout is this module's own invariant: the worktree for `<name>` is at
    `{repo}/fixes/{name}`, or `{repo}/{name}` for the fixed-name `counsel`
    checkout. Where that directory exists, both ends are rewritten to match.
    """
    worktrees_dir = mirror / "worktrees"
    if not worktrees_dir.is_dir():
        return

    for entry in sorted(worktrees_dir.iterdir()):
        if not entry.is_dir():
            continue
        for candidate in (destination / "fixes" / entry.name, destination / entry.name):
            if not candidate.is_dir():
                continue
            try:
                (candidate / ".git").write_text(f"gitdir: {entry}\n")
                (entry / "gitdir").write_text(f"{candidate / '.git'}\n")
            except OSError as error:
                logger.warning("could not repair worktree %s: %s", entry.name, error)
            break
        else:
            # No directory for this entry anywhere under the repo root. It is
            # a dead worktree; `git worktree prune` is the right tool and
            # deleting administrative state is not this function's job.
            logger.info("worktree %s has no directory under %s", entry.name, destination)


def repo_root(github_full_name: str) -> Path:
    """`{workspace}/{org}/{owner}__{repo}` -- the one place this is decided.

    Every caller goes through here rather than composing the path itself.
    That is not style: this codebase has already shipped two bugs where one
    call site built a workspace path differently from another (a hardcoded
    fixture repo in the Fix Council runner, and a poller that only ever
    polled one repo), and both were invisible until something was run for
    real. One function, one layout.
    """
    current = WORKSPACE_ROOT / _org_dir(github_full_name) / repo_slug(github_full_name)
    if _is_dir(current):
        return current

    for legacy in _legacy_candidates(github_full_name, current):
        # The web process mounts the workspace READ-ONLY by design
        # (plan.md §15), so it can find the old tree but must not move it.
        # The worker, which does have write access, performs the move on its
        # next touch of this repo.
        if os.access(WORKSPACE_ROOT, os.W_OK) and _relocate(legacy, current):
            return current
        return legacy

    return current


def mirror_path(github_full_name: str) -> Path:
    return repo_root(github_full_name) / "mirror"


def authenticated_remote(github_full_name: str) -> str:
    """`https://x-access-token:<token>@github.com/owner/repo.git`.

    The username matters. Written as `https://<token>@github.com/...` -- which
    is what this used to build -- git reads the token as a USERNAME with no
    password, then tries to prompt for one. Cloning a public repo never needs
    the credential so it succeeded anyway, and the mistake only surfaced at the
    first PUSH, as `could not read Password ...: No such device or address`
    with no mention of the URL. `x-access-token` is the username GitHub
    documents for token auth and works for both PATs and App installation
    tokens.
    """
    return f"https://x-access-token:{settings.github_token}@github.com/{github_full_name}.git"


def _git_env() -> dict:
    """Never let git block on a credential prompt.

    A non-interactive process with no terminal gets a hard error instead of
    hanging forever -- which is what turned the push failure above into a
    clean, attributable exit rather than a worker stuck holding a queue item.
    """
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env.setdefault("GIT_ASKPASS", "")
    return env


def ensure_mirror(github_full_name: str) -> Path:
    """Clone the bare mirror if it doesn't exist yet; fetch if it does."""
    path = mirror_path(github_full_name)
    path.parent.mkdir(parents=True, exist_ok=True)

    clone_url = authenticated_remote(github_full_name)

    if not path.exists():
        subprocess.run(
            ["git", "clone", "--mirror", clone_url, str(path)],
            check=True,
            capture_output=True,
            text=True,
            env=_git_env(),
        )
    else:
        # Rewrite the remote every time rather than only at clone. Mirrors
        # outlive tokens: an existing clone still carries whatever URL (and
        # whatever expired credential) it was created with, and every worktree
        # inherits that URL, so a mirror cloned before this fix would keep
        # failing to push forever.
        subprocess.run(
            ["git", "--git-dir", str(path), "remote", "set-url", "origin", clone_url],
            check=False,
            capture_output=True,
            text=True,
        )
        # Scoped to `main` only -- NOT a bare `fetch origin`, which (because
        # this is a --mirror clone, whose default refspec is +refs/*:refs/*)
        # tries to update every ref including branches WhipGuard itself pushed
        # back to origin earlier for an open PR. If one of those is currently
        # checked out in an active fix worktree, git refuses the whole fetch
        # outright ("refusing to fetch into branch ... checked out at ...") --
        # found by hitting it for real once a fix's branch existed on origin
        # and a second detection run tried to refresh the mirror.
        subprocess.run(
            ["git", "--git-dir", str(path), "fetch", "origin", "+refs/heads/main:refs/heads/main"],
            check=True,
            capture_output=True,
            text=True,
            env=_git_env(),
        )
    return path


def create_worktree(mirror: Path, issue_number: int, slug: str, base_branch: str = "main") -> Path:
    repo_root = mirror.parent
    worktree_path = repo_root / "fixes" / f"{issue_number}-{slug}"
    (repo_root / "fixes").mkdir(parents=True, exist_ok=True)

    branch_name = f"whipguard/{issue_number}-{slug}"

    # The path is reused, not unique per run. Two cases reach the same
    # directory twice: detect/recheck worktrees use a fixed name
    # (issue_number=0) every run, and -- since the review flow landed -- a
    # REVISION regenerates the patch for the same issue under the same branch
    # name. `git worktree add` refuses a path that already exists, so the
    # second attempt died with a bare exit 255 and the reworked fix simply
    # never appeared. Found by asking for a revision against the real
    # deployment, not by reasoning about the call.
    #
    # Removing the worktree first (rather than only deleting the branch ref)
    # is what actually clears it: the admin entry under mirror/worktrees still
    # holds the path even after the directory is gone.
    if worktree_path.exists():
        remove_worktree(mirror, worktree_path)
    subprocess.run(
        ["git", "--git-dir", str(mirror), "worktree", "prune"],
        check=False,
        capture_output=True,
        text=True,
    )
    # A prior run's branch ref can outlive its worktree, and `-b` refuses to
    # create a branch that already exists. These branches are only ever
    # recreated from `base_branch`, so dropping a stale one loses nothing --
    # anything worth keeping was already pushed at approval.
    subprocess.run(
        ["git", "--git-dir", str(mirror), "branch", "-D", branch_name],
        check=False,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [
            "git",
            "--git-dir",
            str(mirror),
            "worktree",
            "add",
            "-b",
            branch_name,
            str(worktree_path),
            base_branch,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    # Recursive, not just the top directory: `npm install` doesn't only
    # CREATE new entries (node_modules) -- it also REWRITES package-lock.json,
    # an EXISTING checked-out file. A checked-out file's default mode (git's
    # normal 644) denies write to anyone but its owner regardless of the
    # containing directory's own ownership, so chowning only the directory
    # (an earlier version of this fix) still left npm hitting a bare EACCES
    # on that one file -- found by dropping --silent, which had been
    # swallowing the real error behind an opaque, contentless exit 243.
    for root, dirs, files in os.walk(worktree_path):
        for name in dirs + files:
            os.chown(os.path.join(root, name), _SANDBOX_UID, _SANDBOX_GID)
    os.chown(worktree_path, _SANDBOX_UID, _SANDBOX_GID)
    return worktree_path


def remove_worktree(mirror: Path, worktree_path: Path) -> None:
    subprocess.run(
        ["git", "--git-dir", str(mirror), "worktree", "remove", "--force", str(worktree_path)],
        check=False,
        capture_output=True,
        text=True,
    )
    if worktree_path.exists():
        shutil.rmtree(worktree_path, ignore_errors=True)


def _own_for_sandbox(worktree_path: Path) -> None:
    """This process runs as root in the containerized deployment, so anything
    it creates is root-owned and unreadable-for-write by the non-root sandbox
    -- and git refuses to operate on a tree whose owner differs from the
    caller ("dubious ownership"). Same fix create_worktree already applies."""
    for root, directories, files in os.walk(str(worktree_path)):
        for name in directories + files:
            try:
                os.chown(os.path.join(root, name), _SANDBOX_UID, _SANDBOX_GID)
            except OSError:
                pass
    try:
        os.chown(worktree_path, _SANDBOX_UID, _SANDBOX_GID)
    except OSError:
        pass


def ensure_read_worktree(mirror: Path, base_branch: str = "main") -> Path:
    """A persistent, read-only checkout of the default branch for Counsel.

    Counsel answers questions about "the code", which means the current
    default branch -- not a fix worktree, which is one *proposed* change to
    it. The mirror itself is a bare clone with no working files, so BM25
    indexing finds nothing there and reading a file fails outright.

    Kept rather than created per question: building it costs a checkout, and
    a chat that pays that on every message is a chat nobody uses. The worker
    refreshes it whenever it reindexes the repo, which is the same moment the
    indexes Counsel reads are rebuilt.

    Created by the WORKER only -- the web process mounts the workspace
    read-only by design (plan.md §15).
    """
    worktree_path = mirror.parent / "counsel"

    if worktree_path.exists():
        result = subprocess.run(
            ["git", "-C", str(worktree_path), "reset", "--hard", base_branch],
            check=False, capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0:
            _own_for_sandbox(worktree_path)
            return worktree_path
        # Refresh failed (a rebased branch, a corrupt checkout). Rebuilding is
        # cheap and always correct; limping along with a stale tree is not.
        remove_worktree(mirror, worktree_path)

    subprocess.run(
        ["git", "--git-dir", str(mirror), "worktree", "add", "--detach", str(worktree_path), base_branch],
        check=True, capture_output=True, text=True, timeout=120,
    )
    _own_for_sandbox(worktree_path)
    return worktree_path
