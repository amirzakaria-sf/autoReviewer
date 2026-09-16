#!/usr/bin/env bash
#
# WhipGuard deploy: git pull (ff-only), rebuild backend+frontend, recreate
# both containers. Two ways this runs:
#
#   ./deploy.sh                 you, interactively, from a real terminal --
#                                output streams live AND is saved to a log.
#   routers/admin.py's redeploy launches this DETACHED from inside the
#                                backend container -- no one's watching
#                                stdout there, only the saved log matters.
#
# Both are supported from the same script (see LOG_DIR/tee below) rather
# than two separate scripts drifting apart.
#
#   ./deploy.sh              git pull --ff-only, then build and deploy
#   ./deploy.sh --no-pull    deploy the tree as it is now (no git)
#
# Three real bugs were found by actually running this (plan.md's own §9.8
# principle), not by trusting the design on paper:
#
# 1. Originally redirected ALL output straight to a log file as the very
#    first line, unconditionally -- correct for the detached admin-triggered
#    case, but it meant a human running `./deploy.sh` themselves from their
#    own terminal saw nothing at all. `tee` below fixes this: output goes to
#    both places at once instead of picking one.
# 2. `docker compose up -d --force-recreate backend` from INSIDE the backend
#    container is genuinely self-destructive: stopping the OLD container
#    (which a detached run's own process tree lives in) kills that process
#    before the daemon-side create-new-container step it just requested can
#    finish, so the backend never actually comes back. Confirmed live:
#    `docker compose ps` afterward showed backend gone entirely, requiring a
#    manual `docker compose up -d` from the HOST to recover. Fix: the
#    backend's own recreate is delegated to a throwaway SIBLING container
#    (docker:27-cli below) that isn't itself being replaced, so it survives
#    the old backend's teardown. When YOU run this script from the host
#    directly, this script's own process isn't inside any container being
#    torn down, so it survives fine either way -- the sibling-container
#    indirection is what makes the detached case behave the same.
# 3. A bare `docker compose up -d --force-recreate backend frontend` also
#    recreated POSTGRES, unrequested -- compose evaluates the whole project
#    file for drift, not just the services named on the command line.
#    `--no-deps` on every `up` below stops a service recreate from cascading
#    to services it depends_on.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

PULL=1
for arg in "$@"; do
  case "$arg" in
    --no-pull) PULL=0 ;;
    --pull) PULL=1 ;;
    -h|--help) sed -n '/^#   \.\/deploy/,/no git/p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done

if (( PULL )) && [[ -z "${DEPLOY_ALREADY_PULLED:-}" ]]; then
  if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    echo "[deploy] pulling latest source (ff-only)..."
    git pull --ff-only
    echo "[deploy] HEAD is $(git rev-parse --short HEAD) $(git log -1 --pretty=%s)"
    # Re-exec so a just-pulled change to THIS script is the version that
    # actually runs for the rest of the deploy.
    exec env DEPLOY_ALREADY_PULLED=1 "$0" "$@"
  else
    echo "[deploy] not a git checkout -- skipping pull (pass --no-pull to silence this)"
  fi
fi

# /srv/workspace is the container's own path to the real host ./workspace
# directory; when this script runs INSIDE the backend container (the
# detached admin-triggered path) that's the one writable, still-readable-
# after-the-container-dies place to put a log. When YOU run this directly
# on the host, $ROOT/workspace is the same physical directory, reached the
# normal way instead. /.dockerenv (not "does /srv/workspace exist") is the
# actual signal for which case this is -- an empty, root-owned /srv/workspace
# can and does exist on the host too (found by actually running this: a
# stray leftover directory there made the existence check pick the
# container path on the host and fail with Permission denied).
if [[ -f /.dockerenv ]]; then
  LOG_DIR="/srv/workspace"
else
  LOG_DIR="$ROOT/workspace"
fi
mkdir -p "$LOG_DIR"

# tee, not a bare redirect: a bare `exec > log 2>&1` (this script's own
# earlier version) sends output ONLY to the file -- silent to anyone running
# this by hand. Piping through tee keeps it on the terminal too, whenever
# one is attached; when there isn't one (the detached admin-triggered path),
# tee's write to the closed stdout is harmless and the log file still gets
# everything.
exec > >(tee "$LOG_DIR/deploy.log") 2>&1

echo "=== WhipGuard deploy started $(date -u +%FT%TZ) ==="
echo "[deploy] building backend + frontend..."
# `worker` has no build of its own -- it runs the backend image by tag
# (docker-compose.yml explains why), so this one build covers both.
docker compose build backend frontend

echo "[deploy] recreating frontend (safe -- not the container running this script)..."
docker compose up -d --no-deps --force-recreate frontend

# The privileged worker (plan.md §15) runs the SAME image as the backend, so
# a deploy that skipped it would leave the half of the system that actually
# executes repository code running the previous build indefinitely. It is
# also the container that now runs this script when the redeploy is
# triggered from the admin UI, so -- exactly like the backend below -- it
# cannot safely recreate itself and is handed to the sibling container too.

echo "[deploy] handing the backend + worker swap to a disposable sibling container..."
# This container is NOT the one being replaced, so it is unaffected even if
# the old backend container is what's running THIS script and gets stopped
# partway through the command it just issued.
docker run --rm -d \
  --name whipguard-backend-swap \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v "$ROOT:$ROOT:ro" \
  -w "$ROOT" \
  docker:27-cli \
  sh -c "docker compose up -d --no-deps --force-recreate backend worker"

echo "[deploy] waiting for the backend to come back..."
for _ in $(seq 1 40); do
  if curl -fsS -o /dev/null "http://127.0.0.1:8300/healthz" 2>/dev/null; then
    echo "=== WhipGuard deploy finished OK $(date -u +%FT%TZ) ==="
    exit 0
  fi
  sleep 3
done

echo "=== WhipGuard deploy: backend swap dispatched but health check didn't confirm within 2 minutes $(date -u +%FT%TZ) ===" >&2
echo "[deploy] check manually: docker compose ps && curl http://127.0.0.1:8300/healthz" >&2
exit 1
