from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv(Path(__file__).resolve().parents[2] / ".env")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    database_url: str = "postgresql+asyncpg://whipguard:whipguard@localhost:5432/whipguard"

    azure_api_endpoint: str = ""
    azure_api_key: str = ""
    azure_openai_api_version: str = "2024-08-01-preview"
    azure_fast_deployment: str = ""
    azure_worker_deployment: str = ""
    azure_planner_deployment: str = ""
    azure_mechanical_deployment: str = ""
    azure_embedding_deployment: str = "text-embedding-3-small"

    cloudflare_account_id: str = ""
    cloudflare_api_token: str = ""
    cloudflare_pages_project: str = "whipguard-demo-ui"

    slack_bot_token: str = ""
    slack_signing_secret: str = ""
    # Global fallback only -- the real per-repo channel lives on Repo.slack_
    # channel_id (app/routers/slack_connect.py), chosen via Slack's own
    # OAuth channel picker (the incoming-webhook scope's consent screen)
    # rather than anyone hand-typing a channel ID. Repos that haven't
    # connected Slack yet fall back to this if it's set; the actual send
    # always uses the existing workspace-wide bot token either way -- the
    # OAuth flow below is only ever used to pick a channel, never to
    # re-authenticate the bot itself.
    slack_channel_id: str = ""
    slack_client_id: str = ""
    slack_client_secret: str = ""
    slack_oauth_redirect_uri: str = "https://whip-guard.zakarias.in/api/slack/oauth/callback"

    github_token: str = ""
    # OAuth App credentials (Settings -> Developer settings -> OAuth Apps),
    # not a GitHub App -- this type has no private key/JWT auth, only the
    # standard authorization-code exchange below (routers/github.py's
    # /oauth/start + /oauth/callback). A successful exchange overwrites
    # github_token above, both in memory and back into .env, so every
    # existing github_client.py call site keeps working unchanged.
    github_client_id: str = ""
    github_client_secret: str = ""
    github_oauth_redirect_uri: str = "https://whip-guard.zakarias.in/api/github/oauth/callback"

    # Real GitHub App identity (app/integrations/github_app_auth.py) -- a
    # DIFFERENT credential shape than the OAuth App above (App ID + a private
    # key generated on the App's own settings page, not a client secret).
    # Optional: github_client.py falls back to github_token unchanged while
    # these are unset. github_app_private_key is the FULL PEM contents (not
    # a path) -- .env can hold a multi-line value in a quoted string.
    # github_app_installation_id is optional too: left unset, it's
    # auto-discovered via GET /app/installations (fine for a single-org
    # deployment like this one).
    github_app_id: str = ""
    github_app_private_key: str = ""
    github_app_installation_id: str = ""

    fixture_repo: str = "amirzakaria-sf/whipguard-demo-ui"

    # Where THIS process sees the workspace directory. Defaults to the
    # container layout (docker-compose bind-mounts it at /srv/workspace, since
    # the Dockerfile's WORKDIR is /srv) — override via env for local dev (venv),
    # where backend/ is nested under the real repo root instead of BEING the
    # container root. Deliberately an explicit setting, not derived from
    # __file__'s path depth, because that depth differs between the two layouts.
    workspace_root: str = "/srv/workspace"

    # When the backend itself runs inside a container (docker-compose), any path
    # it hands to the HOST's Docker daemon (via the mounted socket) to bind-mount
    # into a sandbox container must be a HOST path, not this container's own view
    # of it — the two differ by whatever the compose volume mapping is. Empty
    # means "not containerized" (local dev via a venv), where the container's own
    # path IS the host path.
    workspace_host_path: str = ""

    # Real host path of the repo root, mounted into this container at that
    # SAME path (docker-compose.yml's `${PWD}:${PWD}:ro`) so `docker compose`
    # commands issued from inside here resolve correctly against the host
    # daemon -- see routers/admin.py's /redeploy and deploy.sh.
    repo_root: str = ""

    assurance_threshold: int = 75
    resolution_threshold: int = 80

    # Transactional email (plan.md §6.4): same Brevo SMTP relay jobFlowAuto
    # already uses on this same domain (zakarias.in) -- reusing a
    # deliverability-proven sender rather than standing up a fresh one.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_key: str = ""
    email_from_address: str = ""
    email_from_name: str = "WhipGuard"
    # Who gets fix-proposed / escalation email. A single operator's inbox for
    # this deployment, same role ADMIN_EMAILS plays in jobFlowAuto.
    notify_email: str = ""

    # App-level login (replaces the earlier nginx HTTP Basic Auth stopgap --
    # that showed the browser's native credential popup ahead of the app,
    # which is what "the login is weird" meant; this is a real session cookie
    # + a dashboard login page instead).
    admin_password: str = "whipguard-demo"
    session_secret: str = "change-me-in-real-deployments"


settings = Settings()
