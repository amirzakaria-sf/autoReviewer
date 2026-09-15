#!/usr/bin/env bash
#
# WhipGuard redeploy: rebuild backend+frontend from the source already on
# disk and recreate both containers. Launched detached (start_new_session)
# by routers/admin.py's POST /api/admin/redeploy. Logs to deploy.log next to
# this script rather than streaming to that request; the admin dashboard's
# Redeploy panel polls GET /api/admin/redeploy/status, which tails this file,
# and polls /healthz directly too.
#
# Two real bugs were found by actually running this once (plan.md's own
# §9.8 principle) rather than trusting the design on paper:
#
# 1. `docker compose up -d --force-recreate backend` from INSIDE the backend
#    container is genuinely self-destructive: stopping the OLD container
#    (which this script's own process tree lives in -- start_new_session
#    only protects against the PARENT PYTHON PROCESS dying, not the whole
#    CONTAINER being torn down) kills this script before the daemon-side
#    create-new-container step it just requested can finish, so the backend
#    never actually comes back. Confirmed live: `docker compose ps` afterward
#    showed backend gone entirely, requiring a manual `docker compose up -d`
#    from the HOST to recover. Fix: the backend's own recreate is delegated
#    to a throwaway SIBLING container (docker:27-cli below) that is not
#    itself being replaced, so it survives the old backend's teardown.
# 2. A bare `docker compose up -d --force-recreate backend frontend` also
#    recreated POSTGRES, unrequested -- compose evaluates the whole project
#    file for drift, not just the services named on the command line, and
#    this repo's docker-compose.yml had genuinely changed this session.
#    `--no-deps` on every `up` below stops a service recreate from cascading
#    to services it depends_on.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

# /srv/workspace is a real host bind mount (./workspace), not this
# container's own ephemeral filesystem -- it's the one place a log written
# here is still readable after the container that wrote it is gone. Hardcoded
# (not read from $WORKSPACE_ROOT) on purpose -- .env sets WORKSPACE_ROOT to a
# different, LOCAL-VENV-ONLY path, and env_file: .env leaks that same
# variable name into this container too; using it here silently pointed this
# log at the read-only repo-root mount instead (found the same way as bug 1).
LOG_DIR="/srv/workspace"
mkdir -p "$LOG_DIR"
exec > "$LOG_DIR/deploy.log" 2>&1

echo "=== WhipGuard redeploy started $(date -u +%FT%TZ) ==="
echo "[deploy] building backend + frontend..."
docker compose build backend frontend

echo "[deploy] recreating frontend (safe -- not the container running this script)..."
docker compose up -d --no-deps --force-recreate frontend

echo "[deploy] handing the backend swap to a disposable sibling container..."
# This container is NOT the one being replaced, so it is unaffected when the
# old backend container is stopped partway through the command it runs.
docker run --rm -d \
  --name whipguard-backend-swap \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v "$ROOT:$ROOT:ro" \
  -w "$ROOT" \
  docker:27-cli \
  sh -c "docker compose up -d --no-deps --force-recreate backend"

echo "[deploy] backend swap handed off -- this script's job ends here."
echo "[deploy] poll http://127.0.0.1:8300/healthz from OUTSIDE any container to confirm."
echo "=== WhipGuard redeploy dispatched OK $(date -u +%FT%TZ) ==="
