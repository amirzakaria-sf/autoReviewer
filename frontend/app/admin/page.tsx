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
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-xl font-semibold">Admin</h1>
          <p className="text-sm text-lo mt-1">Organizations, users, real usage, and deployment control.</p>
        </div>
        <div className="flex gap-2">
          <a href="/admin/orgs" className="btn btn-ghost px-4 py-2 text-sm">Organizations</a>
          <a href="/admin/users" className="btn btn-ghost px-4 py-2 text-sm">Users</a>
        </div>
      </div>

      {error && <div className="badge badge-red">{error}</div>}

      <section className="grid grid-cols-2 sm:grid-cols-4 gap-4">
        <StatCard label="Active users" value={overview?.users.active} color="green" />
        <StatCard label="Pending requests" value={overview?.access_requests.pending} color="yellow" href="/admin/users" />
        <StatCard label="Repos connected" value={overview?.repos} color="blue" />
        <StatCard label="Issues tracked" value={overview?.issues} color="gray" />
      </section>

      {/* Surfaced because a saving that leaves no trace is indistinguishable
          from a feature nobody reached — both were already computed by the
          API and displayed nowhere. */}
      <section className="grid grid-cols-2 sm:grid-cols-4 gap-4">
        <StatCard label="Cached model answers" value={overview?.llm_cache?.entries} color="blue" />
        <StatCard label="Cache hits" value={overview?.llm_cache?.hits} color="green" />
        <StatCard label="Remembered failures" value={overview?.memory_traces} color="yellow" />
      </section>

      <section className="card p-5" style={{ borderColor: "var(--failed-dim)" }}>
        <h2 className="font-semibold mb-1 flex items-center gap-2">
          <span className="badge badge-red">Global kill switch</span>
        </h2>
        <p className="text-xs text-lo mb-4">
          Stops detection or proposals on every connected repo. Per-repo pauses still exist on each repo&apos;s settings page.
        </p>
        <div className="grid sm:grid-cols-2 gap-3">
          <button
            className={`flex items-center justify-between p-3.5 rounded-lg border text-left ${
              overview?.kill_switch?.detection_paused ? "border-[color:var(--failed-dim)] bg-[rgba(255,95,86,0.08)]" : "border-border"
            }`}
            onClick={async () => {
              await api.setKillSwitch({ detection_paused: !overview?.kill_switch?.detection_paused });
              refresh();
            }}
          >
            <div>
              <div className="text-sm font-medium">Pause all detection</div>
              <div className="text-xs text-lo mt-0.5">No new Bug Council scans, from webhooks or the dashboard.</div>
            </div>
          </button>
          <button
            className={`flex items-center justify-between p-3.5 rounded-lg border text-left ${
              overview?.kill_switch?.proposals_paused ? "border-[color:var(--failed-dim)] bg-[rgba(255,95,86,0.08)]" : "border-border"
            }`}
            onClick={async () => {
              await api.setKillSwitch({ proposals_paused: !overview?.kill_switch?.proposals_paused });
              refresh();
            }}
          >
            <div>
              <div className="text-sm font-medium">Pause all fix proposals</div>
              <div className="text-xs text-lo mt-0.5">Raised issues will not get a Fix Council run.</div>
            </div>
          </button>
        </div>
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
          <div className="mb-6">
            <div className="flex items-baseline justify-between mb-2">
              <span className="section-label">Daily token volume</span>
              {/* A bar chart with no scale is decoration. This names the
                  value the tallest bar actually reaches. */}
              <span className="text-[11px] text-lo num">peak {maxDayTokens.toLocaleString()} tok</span>
            </div>
            <div
              className="flex items-end gap-1 h-24"
              style={{ borderBottom: "1px solid var(--ink-700)" }}
            >
              {usage.by_day.map((day) => (
                <div
                  key={day.day}
                  className="rounded-t transition-all"
                  style={{
                    // Capped rather than flex-1: with a single day of data a
                    // full-width bar reads as a solid block, not a chart.
                    flex: "1 1 0",
                    maxWidth: usage.by_day.length < 4 ? 56 : undefined,
                    height: `${Math.max(3, (day.total_tokens / maxDayTokens) * 96)}px`,
                    background: "var(--amber)",
                    opacity: 0.75,
                  }}
                  title={`${day.day}: ${day.total_tokens.toLocaleString()} tokens across ${day.calls} call(s)`}
                />
              ))}
              {/* Keeps a short series left-aligned instead of stretched. */}
              {usage.by_day.length < 4 && <div className="flex-[6]" aria-hidden />}
            </div>
            <div className="flex justify-between mt-1.5 text-[11px] text-lo num">
              <span>{usage.by_day[0]?.day}</span>
              {usage.by_day.length > 1 && <span>{usage.by_day[usage.by_day.length - 1]?.day}</span>}
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

const TONE_BAR: Record<string, string> = {
  green: "var(--verified)",
  yellow: "var(--amber)",
  blue: "var(--pending)",
  gray: "var(--ink-600)",
};

function StatCard({ label, value, color, href }: { label: string; value?: number; color: string; href?: string }) {
  const content = (
    <div className="card card-hover p-4 relative overflow-hidden">
      {/* Same edge-bar language as the overview: the number is the content,
          the tone belongs to the card rather than to a second label. */}
      <span className="absolute left-0 top-0 bottom-0 w-[2px]" style={{ background: TONE_BAR[color] }} aria-hidden />
      <div className="num text-2xl font-semibold tracking-tight">
        {value ?? <span className="skeleton inline-block h-6 w-8 align-middle" />}
      </div>
      <div className="mt-1.5 text-xs text-mid">{label}</div>
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
