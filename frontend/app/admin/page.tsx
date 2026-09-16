"use client";

import { useEffect, useState } from "react";
import { api, type AdminOverview, type UsageStats } from "@/lib/api";

export default function AdminOverviewPage() {
  const [overview, setOverview] = useState<AdminOverview | null>(null);
  const [usage, setUsage] = useState<UsageStats | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [deploying, setDeploying] = useState(false);
  const [deployLog, setDeployLog] = useState<string | null>(null);
  const [deploySucceeded, setDeploySucceeded] = useState<boolean | null>(null);

  async function refresh() {
    try {
      const [ov, us] = await Promise.all([api.adminOverview(), api.usageStats()]);
      setOverview(ov);
      setUsage(us);
      setError(null);
    } catch {
      setError("Could not reach the admin API.");
    }
  }

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 10000);
    return () => clearInterval(interval);
  }, []);

  async function redeploy() {
    if (!confirm("Rebuild and restart WhipGuard from the source currently on disk? The backend will briefly disconnect.")) return;
    setDeploying(true);
    setDeployLog(null);
    setDeploySucceeded(null);
    try {
      await api.triggerRedeploy();
    } catch {
      // The backend may already be mid-restart by the time this call returns.
    }
    pollDeployStatus();
  }

  function pollDeployStatus() {
    const poll = setInterval(async () => {
      try {
        const status = await api.redeployStatus();
        setDeployLog(status.log);
        if (!status.running) {
          setDeploying(false);
          setDeploySucceeded(!!status.succeeded);
          clearInterval(poll);
        }
      } catch {
        // Backend unreachable mid-restart -- keep polling silently.
      }
    }, 3000);
    setTimeout(() => clearInterval(poll), 5 * 60 * 1000);
  }

  const maxDayTokens = Math.max(1, ...(usage?.by_day.map((d) => d.total_tokens) ?? [1]));

  return (
    <div className="space-y-8 animate-fade-in">
      <div>
        <h1 className="text-xl font-semibold">Admin</h1>
        <p className="text-sm text-lo mt-1">Users, real usage, and deployment control.</p>
      </div>

      {error && <div className="badge badge-red">{error}</div>}

      <section className="grid grid-cols-2 sm:grid-cols-4 gap-4">
        <StatCard label="Active users" value={overview?.users.active} color="green" />
        <StatCard label="Pending requests" value={overview?.access_requests.pending} color="yellow" href="/admin/users" />
        <StatCard label="Repos connected" value={overview?.repos} color="blue" />
        <StatCard label="Issues tracked" value={overview?.issues} color="gray" />
      </section>

      <section className="card p-5">
        <div className="flex items-center justify-between mb-4">
          <div>
            <h2 className="font-semibold">Usage — last {usage?.window_days ?? 30} days</h2>
            <p className="text-xs text-lo mt-0.5">Every number here traces to a real council-run row. No cost estimate — this app doesn't guess your Azure pricing tier.</p>
          </div>
        </div>

        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-6">
          <MiniStat label="Model calls" value={usage?.totals.calls} />
          <MiniStat label="Input tokens" value={usage?.totals.input_tokens} />
          <MiniStat label="Output tokens" value={usage?.totals.output_tokens} />
          <MiniStat label="Avg latency" value={usage?.totals.avg_latency_ms} suffix="ms" />
        </div>

        {usage && usage.by_day.length > 0 && (
          <div>
            <div className="section-label mb-2">Daily token volume</div>
            <div className="flex items-end gap-1 h-24">
              {usage.by_day.map((d) => (
                <div key={d.day} className="flex-1 flex flex-col items-center gap-1 group relative">
                  <div
                    className="w-full bg-accent/60 hover:bg-accent rounded-t transition-all"
                    style={{ height: `${Math.max(4, (d.total_tokens / maxDayTokens) * 96)}px` }}
                    title={`${d.day}: ${d.total_tokens} tokens, ${d.calls} calls`}
                  />
                </div>
              ))}
            </div>
          </div>
        )}

        {usage && usage.by_role.length > 0 && (
          <div className="mt-6">
            <div className="section-label mb-2">By role</div>
            <div className="border border-border rounded-lg overflow-hidden">
              <table className="w-full text-sm">
                <thead className="bg-white/5 text-lo text-left text-xs">
                  <tr>
                    <th className="px-3 py-2 font-medium">Role</th>
                    <th className="px-3 py-2 font-medium">Calls</th>
                    <th className="px-3 py-2 font-medium">Input tok.</th>
                    <th className="px-3 py-2 font-medium">Output tok.</th>
                    <th className="px-3 py-2 font-medium">Avg latency</th>
                  </tr>
                </thead>
                <tbody>
                  {usage.by_role.map((r) => (
                    <tr key={r.role} className="border-t border-border">
                      <td className="px-3 py-2 font-medium">{r.role}</td>
                      <td className="px-3 py-2 text-mid">{r.calls}</td>
                      <td className="px-3 py-2 text-mid">{r.input_tokens.toLocaleString()}</td>
                      <td className="px-3 py-2 text-mid">{r.output_tokens.toLocaleString()}</td>
                      <td className="px-3 py-2 text-mid">{Math.round(r.avg_latency_ms)}ms</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </section>

      <section className="card p-5">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="font-semibold">Redeploy</h2>
            <p className="text-xs text-lo mt-0.5 max-w-md">
              Rebuilds backend + frontend from the source on disk. The backend swap runs in a
              disposable sibling container (so it survives the old one being torn down) — this
              page briefly can't reach the API during that swap and resumes on its own once it's back.
            </p>
          </div>
          <button onClick={redeploy} disabled={deploying} className="btn btn-primary px-4 py-2 text-sm shrink-0">
            {deploying ? "Deploying…" : "Redeploy now"}
          </button>
        </div>
        {deploySucceeded !== null && (
          <div className="mt-3 badge badge-green">Build + swap dispatched — this page reaching the API again confirms it's back</div>
        )}
        {deployLog && (
          <pre className="mt-3 text-xs bg-black/40 border border-border rounded-lg p-3 max-h-48 overflow-auto whitespace-pre-wrap">
            {deployLog}
          </pre>
        )}
      </section>
    </div>
  );
}

function StatCard({ label, value, color, href }: { label: string; value?: number; color: string; href?: string }) {
  const content = (
    <div className="card card-hover p-4">
      <div className="text-2xl font-semibold">{value ?? "—"}</div>
      <div className={`mt-1.5 text-xs badge badge-${color}`}>{label}</div>
    </div>
  );
  return href ? <a href={href}>{content}</a> : content;
}

function MiniStat({ label, value, suffix }: { label: string; value?: number; suffix?: string }) {
  return (
    <div>
      <div className="text-xl font-semibold">
        {value !== undefined ? Math.round(value).toLocaleString() : "—"}
        {suffix && <span className="text-sm text-lo ml-0.5">{suffix}</span>}
      </div>
      <div className="text-xs text-lo mt-0.5">{label}</div>
    </div>
  );
}
