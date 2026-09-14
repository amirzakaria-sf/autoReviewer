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

async function getJSON<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { cache: "no-store", credentials: "include" });
  if (res.status === 401) throw new UnauthorizedError();
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return res.json();
}

async function postJSON<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    credentials: "include",
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (res.status === 401) throw new UnauthorizedError();
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
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
  repos: () => getJSON<{ id: string; github_full_name: string; default_branch: string }[]>("/api/repos"),
  login: (password: string) => postJSON<{ ok: boolean }>("/api/auth/login", { password }),
  logout: () => postJSON<{ ok: boolean }>("/api/auth/logout"),
  session: () => getJSON<{ authenticated: boolean }>("/api/auth/session"),
  githubProfile: () => getJSON<GithubProfile>("/api/github/profile"),
  githubRepos: () => getJSON<GithubRepo[]>("/api/github/repos"),
  connectRepo: (full_name: string) => postJSON<{ ok: boolean; repo_id: string }>("/api/github/connect", { full_name }),
  wsUrl: () => {
    if (API_BASE) return API_BASE.replace(/^http/, "ws") + "/ws/activity";
    const protocol = typeof window !== "undefined" && window.location.protocol === "https:" ? "wss" : "ws";
    const host = typeof window !== "undefined" ? window.location.host : "localhost:3300";
    return `${protocol}://${host}/ws/activity`;
  },
};
