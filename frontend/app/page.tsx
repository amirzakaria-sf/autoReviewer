"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api, type Overview, type IssueSummary } from "@/lib/api";
import { StatusBadge } from "@/components/StatusBadge";

const STATUS_FILTERS = [
  { key: "", label: "All" },
  { key: "raised", label: "Raised" },
  { key: "fix-proposed", label: "Fix proposed" },
  { key: "detected-below-threshold", label: "Below threshold" },
  { key: "closed", label: "Closed" },
];

export default function OverviewPage() {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [issues, setIssues] = useState<IssueSummary[]>([]);
  const [filter, setFilter] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [repoId, setRepoId] = useState<string | null>(null);
  const [scanning, setScanning] = useState(false);

  async function refresh() {
    try {
      const [ov, list, repos] = await Promise.all([api.overview(), api.issues(filter || undefined), api.repos()]);
      setOverview(ov);
      setIssues(list);
      if (repos[0]) setRepoId(repos[0].id);
      setError(null);
    } catch (e) {
      setError("Could not reach the WhipGuard API. Is the backend running?");
    }
  }

  async function scanNow() {
    if (!repoId) return;
    setScanning(true);
    await api.scanRepo(repoId);
    setTimeout(() => setScanning(false), 4000);
  }

  async function resolveExternally(issueId: string) {
    await api.triggerFix(issueId);
    refresh();
  }

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 4000);
    return () => clearInterval(interval);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filter]);

  const inFlight = issues.filter((i) => i.status === "raised" || i.status === "fix-proposed");

  return (
    <div className="space-y-8">
      {error && (
        <div className="badge badge-red">{error}</div>
      )}

      <section>
        <div className="flex items-center justify-between mb-4">
          <h1 className="text-xl font-semibold">Overview</h1>
          <button
            onClick={scanNow}
            disabled={scanning || !repoId}
            className="px-3 py-1.5 rounded-md bg-white/10 hover:bg-white/20 text-sm font-medium disabled:opacity-50"
          >
            {scanning ? "Scanning…" : "Scan repo now"}
          </button>
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
          <StatCard label="Raised by AI" value={overview?.raised_by_ai} color="blue" />
          <StatCard label="Resolved & verified" value={overview?.resolved_and_verified} color="green" />
          <StatCard label="Awaiting approval" value={overview?.awaiting_approval} color="yellow" />
          <StatCard label="Failed" value={overview?.failed} color="red" />
        </div>
      </section>

      {inFlight.length > 0 && (
        <section>
          <h2 className="text-sm font-semibold text-gray-400 mb-2">
            In flight now ({inFlight.length}) — running concurrently, not queued one at a time
          </h2>
          <div className="grid sm:grid-cols-2 gap-3">
            {inFlight.map((issue) => (
              <Link
                key={issue.id}
                href={`/issues/${issue.id}`}
                className="border border-border bg-panel rounded-lg p-3 flex items-center justify-between hover:border-gray-500 transition"
              >
                <span className="truncate text-sm">{issue.title}</span>
                <StatusBadge label={issue.badge} color={issue.color} />
              </Link>
            ))}
          </div>
        </section>
      )}

      <section>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-lg font-semibold">Issues &amp; fixes</h2>
          <div className="flex gap-1 text-xs">
            {STATUS_FILTERS.map((f) => (
              <button
                key={f.key}
                onClick={() => setFilter(f.key)}
                className={`px-2.5 py-1 rounded-md border ${
                  filter === f.key ? "border-gray-400 bg-white/10" : "border-border text-gray-400"
                }`}
              >
                {f.label}
              </button>
            ))}
          </div>
        </div>

        <div className="border border-border rounded-lg overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-white/5 text-gray-400 text-left">
              <tr>
                <th className="px-3 py-2 font-medium">Title</th>
                <th className="px-3 py-2 font-medium">Origin</th>
                <th className="px-3 py-2 font-medium">Category</th>
                <th className="px-3 py-2 font-medium">Score</th>
                <th className="px-3 py-2 font-medium">Status</th>
                <th className="px-3 py-2 font-medium"></th>
              </tr>
            </thead>
            <tbody>
              {issues.map((issue) => (
                <tr key={issue.id} className="border-t border-border hover:bg-white/5">
                  <td
                    className="px-3 py-2 cursor-pointer"
                    onClick={() => (window.location.href = `/issues/${issue.id}`)}
                  >
                    {issue.title}
                  </td>
                  <td className="px-3 py-2">
                    <span className={`badge ${issue.origin === "detected" ? "badge-blue" : "badge-gray"}`}>
                      {issue.origin === "detected" ? "🤖 AI-detected" : "👤 filed externally"}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-gray-400">{issue.category}</td>
                  <td className="px-3 py-2 text-gray-400">{issue.assurance_score ?? "—"}</td>
                  <td className="px-3 py-2">
                    <StatusBadge label={issue.badge} color={issue.color} />
                  </td>
                  <td className="px-3 py-2 text-right">
                    {issue.status === "raised" && (
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          resolveExternally(issue.id);
                        }}
                        className="text-xs px-2 py-1 rounded-md bg-white/10 hover:bg-white/20"
                      >
                        Resolve this
                      </button>
                    )}
                  </td>
                </tr>
              ))}
              {issues.length === 0 && (
                <tr>
                  <td colSpan={6} className="px-3 py-6 text-center text-gray-500">
                    No issues yet.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}

function StatCard({ label, value, color }: { label: string; value?: number; color: string }) {
  return (
    <div className="border border-border bg-panel rounded-lg p-4">
      <div className="text-2xl font-semibold">{value ?? "—"}</div>
      <div className={`mt-1 text-xs badge badge-${color}`}>{label}</div>
    </div>
  );
}
