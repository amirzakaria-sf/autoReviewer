// Empty string = same-origin relative paths (/api/..., /ws/...), which is what
// works behind nginx in production (nginx proxies /api/ and /ws/ to the backend
// on the SAME public hostname the browser already loaded the page from). A
// baked-in "http://localhost:8300" would resolve to the VIEWER's own machine
// for anyone loading the dashboard from a different machine than this host —
// only useful for local dev, where it's passed explicitly via env instead.
const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "";

export type Overview = {
  raised_by_ai: number;
  resolved_and_verified: number;
  awaiting_approval: number;
  failed: number;
};

export type IssueSummary = {
  id: string;
  repo_id: string;
  category: string;
  origin: "detected" | "filed-externally";
  github_issue_number: number | null;
  title: string;
  severity: number;
  assurance_score: number | null;
  assurance_rubric: { factors: { factor: string; weight: number; note: string }[]; verdict: string } | null;
  evidence: Record<string, unknown> | null;
  status: string;
  badge: string;
  color: string;
  created_at: string | null;
};

export type OutcomeCheck = {
  agreed: boolean;
  github_state: Record<string, unknown>;
  cloudflare_state: Record<string, unknown>;
  slack_state: Record<string, unknown>;
  dashboard_state: string;
  mismatch_detail: Record<string, string> | null;
  checked_at: string | null;
};

export type FixSummary = {
  id: string;
  issue_id: string;
  resolution_score: number | null;
  resolution_rubric: { factors: { factor: string; weight: number; note: string }[]; verdict: string } | null;
  branch_name: string | null;
  pr_number: number | null;
  preview_url: string | null;
  status: string;
  badge: string;
  color: string;
  approved_by: string | null;
  approved_via: string | null;
  created_at: string | null;
  outcome_check: OutcomeCheck | null;
};

/** One issue plus everything the detail page needs: the repo it belongs to
 *  (so GitHub links are built from real data rather than a hardcoded slug),
 *  the thresholds it was judged against, and its fixes. */
export type IssueDetail = IssueSummary & {
  repo_full_name: string | null;
  assurance_threshold: number | null;
  resolution_threshold: number | null;
  fixes: FixSummary[];
};

/** The server actually rejected this session. Only this means "log out". */
/** Three independent axes per person: permission, expertise, level. */
export type OrgMember = {
  member_id: string;
  user_id: string;
  email: string;
  name: string;
  role: "org_admin" | "member";
  seniority: "sde1" | "sde2" | "sde3" | "staff";
  designations: string[];
};

export type OrgOverview = {
  id: string;
  name: string;
  slug: string;
  role: "org_admin" | "member";
  seniority: string;
  members: OrgMember[];
  designations: { id: string; key: string; label: string }[];
  routing_rules: {
    category: string;
    designation_key: string;
    escalate_at_severity: number;
    min_seniority: string;
  }[];
  github_app_installation_id?: string;
};

export type AssignmentPreview = {
  assignee: OrgMember | null;
  watchers: OrgMember[];
  designation: string;
  /** Why this person, in order. Shown to the reader because assignment by
   *  git history reads as blame unless the reasoning is visible. */
  reasoning: string[];
};

export class UnauthorizedError extends Error {}

/** The fix moved on between render and click. Refresh, do not error. */
export class ReviewConflictError extends Error {}

/** The backend could not be reached. The session is untouched -- this is a
 *  redeploy, a dropped connection, or a gateway blip, and treating it as a
 *  logout is what used to throw users back to /login on every deploy. */
export class OfflineError extends Error {
  constructor(message = "Backend unreachable") {
    super(message);
  }
}

// nginx answers with these while the backend container is restarting.
const TRANSIENT_STATUSES = new Set([502, 503, 504]);
const RETRY_BACKOFF_MS = [250, 600, 1400];

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

type RefreshOutcome = "refreshed" | "rejected" | "unreachable";

// A single in-flight refresh at a time -- several components can each hit a
// 401 at nearly the same moment (overview + issues + repos all poll every
// 4s); without this they'd each fire their own /refresh, and the refresh
// token rotates on every use (app/routers/auth.py), so only the FIRST of a
// concurrent burst would still hold a valid cookie by the time the others
// tried theirs.
let refreshInFlight: Promise<RefreshOutcome> | null = null;

async function tryRefresh(): Promise<RefreshOutcome> {
  if (!refreshInFlight) {
    refreshInFlight = fetch(`${API_BASE}/api/auth/refresh`, { method: "POST", credentials: "include" })
      .then((r): RefreshOutcome => (r.ok ? "refreshed" : r.status === 401 ? "rejected" : "unreachable"))
      .catch((): RefreshOutcome => "unreachable")
      .finally(() => {
        refreshInFlight = null;
      });
  }
  return refreshInFlight;
}

const PUBLIC_ROUTES = new Set(["/", "/login", "/signup", "/accept-invite"]);

function forceLogout() {
  if (typeof window === "undefined") return;
  if (PUBLIC_ROUTES.has(window.location.pathname)) return;
  window.location.href = "/login?expired=1";
}

/** Retries only what is safe to retry. A GET can be replayed freely; a POST
 *  that got a 502 may or may not have already applied server-side, and
 *  silently replaying an approve/reject is worse than surfacing the error. */
async function fetchOnce(path: string, init: RequestInit): Promise<Response> {
  const idempotent = !init.method || init.method.toUpperCase() === "GET";
  const attempts = idempotent ? RETRY_BACKOFF_MS.length + 1 : 1;

  for (let attempt = 0; attempt < attempts; attempt++) {
    if (attempt > 0) await sleep(RETRY_BACKOFF_MS[attempt - 1]);
    try {
      const res = await fetch(`${API_BASE}${path}`, { ...init, credentials: "include" });
      if (TRANSIENT_STATUSES.has(res.status) && attempt < attempts - 1) continue;
      if (TRANSIENT_STATUSES.has(res.status)) throw new OfflineError();
      return res;
    } catch (error) {
      if (error instanceof OfflineError) throw error;
      if (attempt >= attempts - 1) throw new OfflineError();
    }
  }
  throw new OfflineError();
}

// Access tokens live 15 minutes (app/security.py). Rolling them on a
// 10-minute cadence means an open tab never reaches the expiry path at all,
// and waking a sleeping laptop rolls one immediately rather than showing a
// flash of "signed out" while the first request 401s.
const KEEPALIVE_INTERVAL_MS = 10 * 60 * 1000;
const KEEPALIVE_MIN_GAP_MS = 60 * 1000;
let lastKeepaliveAt = 0;

export function startSessionKeepalive(): () => void {
  if (typeof window === "undefined") return () => {};

  const roll = () => {
    const now = Date.now();
    if (now - lastKeepaliveAt < KEEPALIVE_MIN_GAP_MS) return;
    lastKeepaliveAt = now;
    void tryRefresh();
  };

  const timer = window.setInterval(roll, KEEPALIVE_INTERVAL_MS);
  const onVisible = () => {
    if (document.visibilityState === "visible") roll();
  };
  document.addEventListener("visibilitychange", onVisible);
  window.addEventListener("online", roll);

  return () => {
    window.clearInterval(timer);
    document.removeEventListener("visibilitychange", onVisible);
    window.removeEventListener("online", roll);
  };
}

async function authedFetch(path: string, init: RequestInit): Promise<Response> {
  let res = await fetchOnce(path, init);
  if (res.status !== 401) return res;

  const outcome = await tryRefresh();
  // An unreachable backend says nothing about whether the session is valid,
  // so it must never end in forceLogout().
  if (outcome === "unreachable") throw new OfflineError();
  if (outcome === "refreshed") {
    res = await fetchOnce(path, init);
    if (res.status !== 401) return res;
  }

  forceLogout();
  throw new UnauthorizedError();
}

async function getJSON<T>(path: string): Promise<T> {
  const res = await authedFetch(path, { cache: "no-store" });
  if (!res.ok) {
    const detail = await res.json().catch(() => null);
    throw new Error(detail?.detail || `${path} -> ${res.status}`);
  }
  return res.json();
}

async function postJSON<T>(path: string, body?: unknown): Promise<T> {
  const res = await authedFetch(path, {
    method: "POST",
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => null);
    const message = detail?.detail || `${path} -> ${res.status}`;
    // 409 means the request was fine and the thing moved on -- someone
    // approved from Slack, or a second click landed. The caller refreshes and
    // shows what actually happened instead of rendering a form error.
    throw res.status === 409 ? new ReviewConflictError(message) : new Error(message);
  }
  return res.json();
}

async function putJSON<T>(path: string, body: unknown): Promise<T> {
  const res = await authedFetch(path, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => null);
    throw new Error(detail?.detail || `${path} -> ${res.status}`);
  }
  return res.json();
}

async function deleteJSON<T>(path: string): Promise<T> {
  const res = await authedFetch(path, { method: "DELETE" });
  if (!res.ok) {
    const detail = await res.json().catch(() => null);
    const message = detail?.detail || `${path} -> ${res.status}`;
    throw res.status === 409 ? new ReviewConflictError(message) : new Error(message);
  }
  return res.json();
}

async function patchJSON<T>(path: string, body: unknown): Promise<T> {
  const res = await authedFetch(path, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => null);
    throw new Error(detail?.detail || `${path} -> ${res.status}`);
  }
  return res.json();
}

export type GithubProfile = {
  connected: boolean;
  login?: string;
  name?: string;
  avatar_url?: string;
  html_url?: string;
};

export type GithubRepo = {
  full_name: string;
  private: boolean;
  default_branch: string;
  html_url: string;
  connected: boolean;
};

export type HumanInputRequest = {
  id: string;
  issue_id: string | null;
  fix_id: string | null;
  node_name: string;
  kind: string;
  question: string;
  options: { id: string; label: string }[] | null;
  status: string;
  created_at: string | null;
};

export type SessionInfo = {
  authenticated: boolean;
  email?: string;
  role?: "admin" | "member";
  onboarding_completed?: boolean;
};

export type MyProfile = {
  email: string;
  first_name: string | null;
  last_name: string | null;
  mobile_number: string | null;
  role: "admin" | "member";
  onboarding_completed: boolean;
  created_at: string | null;
  /** Account-level Slack: one workspace, one channel, all repos. */
  slack: {
    connected: boolean;
    channel_id: string;
    channel_name: string;
    has_token: boolean;
  };
};

export type AdminUser = {
  id: string;
  email: string;
  role: "admin" | "member";
  status: "active" | "deactivated";
  created_at: string | null;
  last_login_at: string | null;
};

export type AccessRequest = {
  id: string;
  name: string;
  email: string;
  reason: string;
  status: "pending" | "approved" | "rejected";
  created_at: string | null;
  decided_at: string | null;
  decision_reason: string | null;
  invite_consumed: boolean;
};

export type AdminOverview = {
  users: { active: number; deactivated: number };
  access_requests: { pending: number; approved: number; rejected: number };
  repos: number;
  issues: number;
  fixes: number;
  /** Proof the response cache is doing something — a saving that leaves no
   *  trace is indistinguishable from a feature nobody reached. */
  llm_cache?: { entries: number; hits: number };
  /** How much the memory layer has actually accumulated. */
  memory_traces?: number;
  kill_switch?: { detection_paused: boolean; proposals_paused: boolean };
};

export type UsageStats = {
  window_days: number;
  totals: { calls: number; input_tokens: number; cached_input_tokens: number; output_tokens: number; avg_latency_ms: number };
  by_role: { role: string; calls: number; input_tokens: number; output_tokens: number; avg_latency_ms: number }[];
  by_day: { day: string; calls: number; total_tokens: number }[];
};

export type CategorySetting = {
  key: string;
  label: string;
  issues_enabled: boolean;
  fixes_enabled: boolean;
  assurance_threshold: number;
  resolution_threshold: number;
  default_assurance_threshold: number;
  default_resolution_threshold: number;
};

export type RepoSettings = {
  id: string;
  github_full_name: string;
  ask_mode: "autonomous" | "balanced" | "verbose";
  detection_paused: boolean;
  proposals_paused: boolean;
  cloudflare_pages_project: string;
  categories: CategorySetting[];
};

export type ReviewTurn = {
  role: "council" | "human" | "system";
  text: string;
  at: string;
  kind?: string;
  fix_id?: string;
};

export type ReviewFix = {
  id: string;
  attempt: number;
  status: string;
  badge: string;
  color: string;
  score: number | null;
  verdict: string | null;
  rubric: { factor?: string; note?: string; weight?: number }[];
  diff: string;
  branch_name: string | null;
  pr_number: number | null;
  preview_url: string | null;
  decision_note: string | null;
};

export type FixReview = {
  issue_id: string;
  issue_title: string;
  status: "awaiting-decision" | "revising" | "approved" | "rejected" | null;
  attempts: number;
  revisions_left: number;
  transcript: ReviewTurn[];
  current: ReviewFix | null;
  history: ReviewFix[];
};

export type OrgSummary = {
  id: string;
  name: string;
  slug: string;
  created_at: string | null;
  member_count: number;
  repo_count: number;
  pending_invites: number;
};

export type OrgInvite = {
  id: string;
  email: string;
  name: string;
  role: string;
  seniority: string;
  designations: string[];
  created_at?: string | null;
};

export type OrgInviteCheck = {
  email: string;
  name: string;
  org_name: string;
  role: string;
  seniority: string;
  needs_password: boolean;
};

export type IdentityLink = {
  id: string;
  provider: string;
  external_id: string;
  user_id: string;
  email: string;
};

export type IdentityOverview = {
  links: IdentityLink[];
  unlinked_authors: { external_id: string; commits: number }[];
};

export type RoutingRule = {
  category: string;
  designation_key: string;
  escalate_at_severity: number;
  min_seniority: string;
};

export const api = {
  pendingClarifications: () => getJSON<HumanInputRequest[]>("/api/human-input?status=pending"),
  answerClarification: (id: string, answer: string) =>
    postJSON<{ ok: boolean }>(`/api/human-input/${id}/answer`, { answer }),
  overview: () => getJSON<Overview>("/api/overview"),
  issues: (status?: string) => getJSON<IssueSummary[]>(`/api/issues${status ? `?status=${status}` : ""}`),
  issue: (id: string) => getJSON<IssueDetail>(`/api/issues/${id}`),
  approveFix: (id: string, note = "") => postJSON(`/api/fixes/${id}/approve`, { note }),
  rejectFix: (id: string, note = "") => postJSON(`/api/fixes/${id}/reject`, { note }),
  fixReview: (issueId: string) => getJSON<FixReview>(`/api/issues/${issueId}/review`),
  reviseFix: (id: string, instruction: string) =>
    postJSON<{ ok: boolean; status: string; queued: boolean }>(`/api/fixes/${id}/revise`, { instruction }),
  scanRepo: (repoId: string) => postJSON(`/api/repos/${repoId}/scan`),
  triggerFix: (issueId: string) => postJSON(`/api/issues/${issueId}/trigger-fix`),
  repos: () =>
    getJSON<
      {
        id: string;
        github_full_name: string;
        default_branch: string;
        detection_paused: boolean;
        proposals_paused: boolean;
      }[]
    >(
      "/api/repos"
    ),
  repoSettings: (repoId: string) => getJSON<RepoSettings>(`/api/repos/${repoId}/settings`),
  updateRepoSettings: (repoId: string, patch: Record<string, unknown>) =>
    patchJSON<{ ok: boolean }>(`/api/repos/${repoId}/settings`, patch),

  orgInvites: () => getJSON<OrgInvite[]>("/api/org/invites"),
  inviteToOrg: (body: { email: string; name?: string; role?: string; seniority?: string; designations?: string[] }) =>
    postJSON<{ ok: boolean; invite: OrgInvite; email_sent: boolean; link: string }>("/api/org/invites", body),
  revokeOrgInvite: (id: string) => deleteJSON<{ ok: boolean }>(`/api/org/invites/${id}`),
  removeOrgMember: (id: string) => deleteJSON<{ ok: boolean; members: OrgMember[] }>(`/api/org/members/${id}`),

  upsertDesignation: (key: string, label: string) =>
    postJSON<{ ok: boolean; designations: { id: string; key: string; label: string }[] }>(
      "/api/org/designations",
      { key, label },
    ),
  deleteDesignation: (key: string) =>
    deleteJSON<{ ok: boolean; designations: { id: string; key: string; label: string }[] }>(
      `/api/org/designations/${encodeURIComponent(key)}`,
    ),
  setRoutingRule: (
    category: string,
    body: { designation_key: string; escalate_at_severity: number; min_seniority: string },
  ) => putJSON<{ ok: boolean; routing_rules: RoutingRule[] }>(`/api/org/routing-rules/${category}`, body),
  identityLinks: () => getJSON<IdentityOverview>("/api/org/identity-links"),
  linkIdentity: (body: { user_id: string; external_id: string; provider?: string }) =>
    postJSON<{ ok: boolean }>("/api/org/identity-links", body),
  unlinkIdentity: (id: string) => deleteJSON<{ ok: boolean; links: IdentityLink[] }>(`/api/org/identity-links/${id}`),

  adminOrgs: () => getJSON<OrgSummary[]>("/api/admin/orgs"),
  createOrg: (body: { name: string; slug?: string; admin_email?: string }) =>
    postJSON<{ ok: boolean; org: OrgSummary; admin: string | null; invite_link: string | null; warning?: string }>(
      "/api/admin/orgs",
      body,
    ),

  checkOrgInvite: (token: string) => getJSON<OrgInviteCheck>(`/api/auth/org-invite?token=${encodeURIComponent(token)}`),
  acceptOrgInvite: (token: string, password: string) =>
    postJSON<{ ok: boolean; role: string }>("/api/auth/accept-org-invite", { token, password }),

  requestAccess: (name: string, email: string, reason: string) =>
    postJSON<{ ok: boolean; status: "pending" | "approved"; invite_token?: string }>("/api/auth/request-access", {
      name,
      email,
      reason,
    }),
  checkInvite: (token: string) => getJSON<{ name: string; email: string }>(`/api/auth/invite?token=${encodeURIComponent(token)}`),
  completeInvite: (token: string, password: string) =>
    postJSON<{ ok: boolean; role: string }>("/api/auth/complete-invite", { token, password }),
  login: (email: string, password: string) => postJSON<{ ok: boolean; role: string }>("/api/auth/login", { email, password }),
  logout: () => postJSON<{ ok: boolean }>("/api/auth/logout"),
  session: () => getJSON<SessionInfo>("/api/auth/session"),

  adminUsers: () => getJSON<AdminUser[]>("/api/admin/users"),
  setUserRole: (id: string, role: "admin" | "member") => postJSON<{ ok: boolean }>(`/api/admin/users/${id}/role`, { role }),
  deactivateUser: (id: string) => postJSON<{ ok: boolean }>(`/api/admin/users/${id}/deactivate`),
  reactivateUser: (id: string) => postJSON<{ ok: boolean }>(`/api/admin/users/${id}/reactivate`),

  accessRequests: () => getJSON<AccessRequest[]>("/api/admin/access-requests"),
  approveAccessRequest: (id: string, orgId = "") =>
    postJSON<{ ok: boolean; request: AccessRequest }>(`/api/admin/access-requests/${id}/approve`, { org_id: orgId }),
  addOrgAdmin: (orgId: string, email: string) =>
    postJSON<{ ok: boolean; admin: string | null; invite_link: string | null }>(
      `/api/admin/orgs/${orgId}/admins`,
      { email },
    ),
  rejectAccessRequest: (id: string, reason: string) =>
    postJSON<{ ok: boolean; request: AccessRequest }>(`/api/admin/access-requests/${id}/reject`, { reason }),
  adminOverview: () => getJSON<AdminOverview>("/api/admin/overview"),
  setKillSwitch: (body: { detection_paused?: boolean; proposals_paused?: boolean }) =>
    postJSON<{ detection_paused: boolean; proposals_paused: boolean }>("/api/admin/kill-switch", body),
  usageStats: () => getJSON<UsageStats>("/api/admin/usage"),
  triggerRedeploy: () => postJSON<{ ok: boolean; message: string }>("/api/admin/redeploy"),
  redeployStatus: () => getJSON<{ running: boolean; succeeded?: boolean; log: string | null }>("/api/admin/redeploy/status"),

  githubProfile: () => getJSON<GithubProfile>("/api/github/profile"),
  githubRepos: () => getJSON<GithubRepo[]>("/api/github/repos"),
  connectRepo: (full_name: string) => postJSON<{ ok: boolean; repo_id: string }>("/api/github/connect", { full_name }),
  disconnectGithub: () => postJSON<{ ok: boolean }>("/api/github/disconnect"),
  disconnectSlack: () => postJSON<{ ok: boolean }>("/api/slack/disconnect"),
  testSlack: () => postJSON<{ ok: boolean; channel: string }>("/api/slack/test"),

  me: () => getJSON<MyProfile>("/api/me"),
  org: () => getJSON<OrgOverview>("/api/org"),
  updateOrg: (patch: Record<string, unknown>) => patchJSON<{ ok: boolean; github_app_installation_id: string }>("/api/org", patch),
  updateOrgMember: (memberId: string, patch: Record<string, unknown>) =>
    patchJSON<{ ok: boolean; members: OrgMember[] }>(`/api/org/members/${memberId}`, patch),
  previewAssignment: (body: { category: string; severity: number; author?: string }) =>
    postJSON<AssignmentPreview>("/api/org/preview-assignment", body),
  updateMe: (patch: { first_name?: string; last_name?: string; mobile_number?: string }) =>
    patchJSON<{ ok: boolean; profile: MyProfile }>("/api/me", patch),
  changePassword: (currentPassword: string, newPassword: string) =>
    postJSON<{ ok: boolean }>("/api/me/password", { current_password: currentPassword, new_password: newPassword }),
  completeOnboarding: () => postJSON<{ ok: boolean }>("/api/me/onboarding/complete"),

  wsUrl: () => {
    if (API_BASE) return API_BASE.replace(/^http/, "ws") + "/ws/activity";
    const protocol = typeof window !== "undefined" && window.location.protocol === "https:" ? "wss" : "ws";
    const host = typeof window !== "undefined" ? window.location.host : "localhost:3300";
    return `${protocol}://${host}/ws/activity`;
  },
};
