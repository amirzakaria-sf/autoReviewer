const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8300";

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
};

async function getJSON<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return res.json();
}

export const api = {
  overview: () => getJSON<Overview>("/api/overview"),
  issues: (status?: string) => getJSON<IssueSummary[]>(`/api/issues${status ? `?status=${status}` : ""}`),
  issue: (id: string) => getJSON<IssueSummary & { fixes: FixSummary[] }>(`/api/issues/${id}`),
  approveFix: (id: string) =>
    fetch(`${API_BASE}/api/fixes/${id}/approve`, { method: "POST" }).then((r) => r.json()),
  rejectFix: (id: string) =>
    fetch(`${API_BASE}/api/fixes/${id}/reject`, { method: "POST" }).then((r) => r.json()),
  scanRepo: (repoId: string) =>
    fetch(`${API_BASE}/api/repos/${repoId}/scan`, { method: "POST" }).then((r) => r.json()),
  triggerFix: (issueId: string) =>
    fetch(`${API_BASE}/api/issues/${issueId}/trigger-fix`, { method: "POST" }).then((r) => r.json()),
  repos: () => getJSON<{ id: string; github_full_name: string; default_branch: string }[]>("/api/repos"),
  wsUrl: () => (API_BASE.replace("http", "ws") + "/ws/activity"),
};
