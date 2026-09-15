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

export class UnauthorizedError extends Error {}

// A single in-flight refresh at a time -- several components can each hit a
// 401 at nearly the same moment (overview + issues + repos all poll every
// 4s); without this they'd each fire their own /refresh, and the refresh
// token rotates on every use (app/routers/auth.py), so only the FIRST of a
// concurrent burst would still hold a valid cookie by the time the others
// tried theirs -- the others would wrongly log the user out.
let refreshInFlight: Promise<boolean> | null = null;

async function tryRefresh(): Promise<boolean> {
  if (!refreshInFlight) {
    refreshInFlight = fetch(`${API_BASE}/api/auth/refresh`, { method: "POST", credentials: "include" })
      .then((r) => r.ok)
      .catch(() => false)
      .finally(() => {
        refreshInFlight = null;
      });
  }
  return refreshInFlight;
}

function forceLogout() {
  if (typeof window === "undefined") return;
  if (window.location.pathname === "/login" || window.location.pathname === "/") return;
  window.location.href = "/login?expired=1";
}

async function authedFetch(path: string, init: RequestInit): Promise<Response> {
  let res = await fetch(`${API_BASE}${path}`, { ...init, credentials: "include" });
  if (res.status === 401) {
    const refreshed = await tryRefresh();
    if (refreshed) {
      res = await fetch(`${API_BASE}${path}`, { ...init, credentials: "include" });
    }
    if (res.status === 401) {
      forceLogout();
      throw new UnauthorizedError();
    }
  }
  return res;
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
    throw new Error(detail?.detail || `${path} -> ${res.status}`);
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
  slack_channel_id: string | null;
  slack_channel_name: string | null;
  categories: CategorySetting[];
};

export const api = {
  pendingClarifications: () => getJSON<HumanInputRequest[]>("/api/human-input?status=pending"),
  answerClarification: (id: string, answer: string) =>
    postJSON<{ ok: boolean }>(`/api/human-input/${id}/answer`, { answer }),
  overview: () => getJSON<Overview>("/api/overview"),
  issues: (status?: string) => getJSON<IssueSummary[]>(`/api/issues${status ? `?status=${status}` : ""}`),
  issue: (id: string) => getJSON<IssueSummary & { fixes: FixSummary[] }>(`/api/issues/${id}`),
  approveFix: (id: string) => postJSON(`/api/fixes/${id}/approve`),
  rejectFix: (id: string) => postJSON(`/api/fixes/${id}/reject`),
  scanRepo: (repoId: string) => postJSON(`/api/repos/${repoId}/scan`),
  triggerFix: (issueId: string) => postJSON(`/api/issues/${issueId}/trigger-fix`),
  repos: () =>
    getJSON<{ id: string; github_full_name: string; default_branch: string; detection_paused: boolean; proposals_paused: boolean }[]>(
      "/api/repos"
    ),
  repoSettings: (repoId: string) => getJSON<RepoSettings>(`/api/repos/${repoId}/settings`),
  updateRepoSettings: (repoId: string, patch: Record<string, unknown>) =>
    patchJSON<{ ok: boolean }>(`/api/repos/${repoId}/settings`, patch),

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
  approveAccessRequest: (id: string) => postJSON<{ ok: boolean; request: AccessRequest }>(`/api/admin/access-requests/${id}/approve`),
  rejectAccessRequest: (id: string, reason: string) =>
    postJSON<{ ok: boolean; request: AccessRequest }>(`/api/admin/access-requests/${id}/reject`, { reason }),
  adminOverview: () => getJSON<AdminOverview>("/api/admin/overview"),
  usageStats: () => getJSON<UsageStats>("/api/admin/usage"),
  triggerRedeploy: () => postJSON<{ ok: boolean; message: string }>("/api/admin/redeploy"),
  redeployStatus: () => getJSON<{ running: boolean; succeeded?: boolean; log: string | null }>("/api/admin/redeploy/status"),

  githubProfile: () => getJSON<GithubProfile>("/api/github/profile"),
  githubRepos: () => getJSON<GithubRepo[]>("/api/github/repos"),
  connectRepo: (full_name: string) => postJSON<{ ok: boolean; repo_id: string }>("/api/github/connect", { full_name }),
  disconnectGithub: () => postJSON<{ ok: boolean }>("/api/github/disconnect"),
  disconnectSlack: (repoId: string) => postJSON<{ ok: boolean }>(`/api/slack/disconnect?repo_id=${repoId}`),

  me: () => getJSON<MyProfile>("/api/me"),
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
