from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv(Path(__file__).resolve().parents[2] / ".env")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    database_url: str = "postgresql+asyncpg://whipguard:whipguard@localhost:5432/whipguard"

    azure_api_endpoint: str = ""
    azure_api_key: str = ""
    # Chat Completions fallback only. The Responses API path pins
    # api_version="preview" against {endpoint}/openai/v1/ instead -- a
    # different surface with a different versioning scheme, which is why
    # this setting does not reach it (app/azure_client.py).
    azure_openai_api_version: str = "2025-04-01-preview"
    azure_fast_deployment: str = ""
    azure_worker_deployment: str = ""
    azure_planner_deployment: str = ""
    azure_mechanical_deployment: str = ""
    azure_embedding_deployment: str = "text-embedding-3-small"

    # Generation protocol. "responses" is the default and the only one that
    # can carry native reasoning alongside tools on GPT-5.x. "chat" is an
    # operator hatch: it pins every generation call to Chat Completions
    # without counting as a protocol failure, for the case where the
    # Responses surface is degraded and someone needs the product working
    # more than they need the reasoning.
    whipguard_azure_api: str = "responses"
    azure_responses_max_output_tokens: int = 16000

    # Web research (app/research.py). The Responses API's own `web_search`
    # tool, run on our own deployments -- NOT the Azure AI Foundry agent
    # indirection the sibling `opencode` project uses. That agent's entire
    # definition is one `{"type": "web_search"}` tool, so the extra project
    # endpoint, extra key and extra hop buy nothing; and as of 2026-09-20 it
    # is broken anyway (its definition names a `gpt-5.2-chat` deployment
    # that no longer exists, so every call 404s DeploymentNotFound).
    web_research_enabled: bool = True
    # Empty means "the worker deployment". Search is a reasoning task, not a
    # cheap one: the model has to decide what to query and what the results
    # mean, and the fast deployment answers that worse for very little less.
    web_research_deployment: str = ""
    web_research_max_chars: int = 6000
    # How many findings a curator may keep from one research run. A cap here
    # is what stops a research stage from quietly becoming the largest single
    # contributor to a prompt's token budget.
    web_research_max_findings: int = 8
    # Per-run ceiling on searches, so an autonomous tool call cannot turn one
    # PRD or one patch attempt into an unbounded crawl.
    web_research_max_calls_per_run: int = 4

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
    # Shared secret GitHub sends as X-Hub-Signature-256. Empty means the
    # webhook handler logs a warning and still accepts events (local/dev).
    # Set this in any deployment that is reachable from the internet.
    github_webhook_secret: str = ""

    fixture_repo: str = "amirzakaria-sf/whipguard-demo-ui"

    # Where this dashboard is reachable from the outside. Used to build the
    # review link that Slack and email point at. Several older call sites
    # still hardcode the same string inline; this is the one that new code
    # reads, and the place to consolidate them on.
    public_base_url: str = "https://whip-guard.zakarias.in"

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
    # Production must set ADMIN_PASSWORD and SESSION_SECRET. Tests and a
    # local venv may flip this so the process can boot with the demo values.
    allow_insecure_defaults: bool = False

    # Context assembly (app/prompt_compiler.py). One budget for the whole
    # prompt rather than a per-injection-point character cap; the encoding
    # is a property of the DEPLOYMENT, so it is configurable rather than
    # hardcoded to whatever the current model family happens to use.
    prompt_budget_tokens: int = 32000
    prompt_tokenizer_encoding: str = "o200k_base"

    # Where the BM25 index is cached. NOT inside the workspace: the
    # web-facing process mounts the workspace read-only (plan.md §15), so a
    # cache under it means every retrieval from the chat agent fails to
    # persist its index and silently rebuilds on every single query.
    retrieval_cache_dir: str = "/srv/cache/retrieval"

    # Retrieval (app/hybrid_retrieval.py). Disabling falls the councils back
    # to the dense-only path they used before fusion existed.
    hybrid_retrieval_enabled: bool = True
    hybrid_retrieval_max_chunks: int = 6
    hybrid_retrieval_max_chars: int = 24000
    hybrid_retrieval_timeout_seconds: float = 8.0

    # Negative-trace memory (app/memory_traces.py).
    memory_traces_enabled: bool = True

    # Web Push (app/push.py). Same VAPID keypair as the sibling `opencode`
    # deployment on this host -- a VAPID key identifies the SENDER, not the
    # app, so sharing one is legitimate and saves rotating two.
    #
    # The public key is a base64url P-256 point (87 chars, starts with "B");
    # the private key is the raw 43-char scalar, NOT a PEM. Getting that
    # wrong produces a 403 BadJwtToken from Apple and nothing else.
    vapid_public_key: str = ""
    vapid_private_key: str = ""
    # Must be a mailto: or https: URI by the time it reaches the push service.
    # app/push.py normalises it, because a value that already carries the
    # scheme would otherwise become `mailto:mailto:...` -- which Apple rejects
    # with no symptom other than pushes never arriving.
    vapid_admin_email: str = ""

    # Exact-match LLM response cache (plan.md §13.2, app/llm_cache.py).
    llm_cache_enabled: bool = True
    llm_cache_ttl_seconds: int = 24 * 3600


settings = Settings()
