"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api, type Overview, type IssueSummary, type HumanInputRequest } from "@/lib/api";
import { StatusBadge } from "@/components/StatusBadge";

/* Filtering happens client-side against one unfiltered fetch. The list is
   small, and it means switching views is instant rather than a round-trip.

   "Needs attention" is the default because the real data is dominated by
   below-threshold findings -- on the fixture repo, 24 of 33 rows were
   findings that by definition nobody acts on, which buried the 9 that
   actually wanted a decision. Those rows are still one click away; they are
   just no longer the first thing you see. */
const ACTIONABLE = ["raised", "fix-proposed"];

const STATUS_FILTERS: { key: string; label: string; match: (status: string) => boolean }[] = [
  { key: "actionable", label: "Needs attention", match: (s) => ACTIONABLE.includes(s) },
  { key: "", label: "All", match: () => true },
  { key: "raised", label: "Raised", match: (s) => s === "raised" },
  { key: "fix-proposed", label: "Fix proposed", match: (s) => s === "fix-proposed" },
  { key: "detected-below-threshold", label: "Below threshold", match: (s) => s === "detected-below-threshold" },
  { key: "closed", label: "Closed", match: (s) => s === "closed" },
];

export default function OverviewPage() {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [issues, setIssues] = useState<IssueSummary[]>([]);
  const [filter, setFilter] = useState("actionable");
  const [error, setError] = useState<string | null>(null);
  const [repoId, setRepoId] = useState<string | null>(null);
  const [scanning, setScanning] = useState(false);
  const [clarifications, setClarifications] = useState<HumanInputRequest[]>([]);
  const [answers, setAnswers] = useState<Record<string, string>>({});

  async function refresh() {
    try {
      const [ov, list, repos, pending] = await Promise.all([
        api.overview(),
        api.issues(),
        api.repos(),
        api.pendingClarifications(),
      ]);
      setOverview(ov);
      setIssues(list);
      if (repos[0]) setRepoId(repos[0].id);
      setClarifications(pending);
      setError(null);
    } catch (e) {
      setError("Could not reach the WhipGuard API. Is the backend running?");
    }
  }

  async function submitAnswer(id: string) {
    const answer = answers[id];
    if (!answer) return;
    await api.answerClarification(id, answer);
    refresh();
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
  }, []);

  const inFlight = issues.filter((i) => ACTIONABLE.includes(i.status));
  const activeFilter = STATUS_FILTERS.find((f) => f.key === filter) ?? STATUS_FILTERS[1];
  const visibleIssues = issues.filter((issue) => activeFilter.match(issue.status));

  return (
    <div className="space-y-8 animate-fade-in">
      {error && <div className="badge badge-red">{error}</div>}

      <section>
        <div className="flex items-center justify-between mb-4">
          <div>
            <h1 className="text-lg font-semibold">Overview</h1>
            <p className="text-xs text-lo mt-0.5">
              Press <kbd className="num px-1 py-0.5 rounded" style={{ background: "var(--ink-750)", border: "1px solid var(--ink-600)" }}>⌘K</kbd> to jump anywhere
            </p>
          </div>
          <button onClick={scanNow} disabled={scanning || !repoId} className="btn btn-ghost px-3.5 py-2">
            {scanning ? "Scanning…" : "Scan repo now"}
          </button>
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <StatCard label="Raised by AI" value={overview?.raised_by_ai} tone="violet" />
          <StatCard label="Resolved & verified" value={overview?.resolved_and_verified} tone="green" />
          <StatCard label="Awaiting approval" value={overview?.awaiting_approval} tone="amber" />
          <StatCard label="Failed" value={overview?.failed} tone="red" />
        </div>
      </section>

      {clarifications.length > 0 && (
        <section className="card p-5 space-y-4" style={{ borderColor: "var(--amber-dim)" }}>
          <h2 className="font-semibold text-sm flex items-center gap-2">
            <span className="badge badge-amber">Needs your input</span>
            <span className="text-mid font-normal">The council paused instead of guessing ({clarifications.length})</span>
          </h2>
          {clarifications.map((c) => (
            <div key={c.id} className="hairline pt-3 first:border-t-0 first:pt-0">
              <p className="text-sm mb-2">{c.question}</p>
              {c.options ? (
                <div className="flex flex-wrap gap-2">
                  {c.options.map((opt) => (
                    <button
                      key={opt.id}
                      onClick={() => api.answerClarification(c.id, opt.label).then(refresh)}
                      className="btn btn-ghost px-3 py-1.5"
                    >
                      {opt.label}
                    </button>
                  ))}
                </div>
              ) : (
                <div className="flex gap-2">
                  <input
                    value={answers[c.id] || ""}
                    onChange={(e) => setAnswers({ ...answers, [c.id]: e.target.value })}
                    placeholder="Your answer…"
                    className="input flex-1 px-2.5 py-1.5 text-sm"
                  />
                  <button onClick={() => submitAnswer(c.id)} className="btn btn-ghost px-3 py-1.5">
                    Answer
                  </button>
                </div>
              )}
            </div>
          ))}
        </section>
      )}

      {inFlight.length > 0 && (
        <section>
          <h2 className="section-label mb-2.5">
            In flight now ({inFlight.length}) — running concurrently, not queued
          </h2>
          <div className="grid sm:grid-cols-2 gap-3">
            {inFlight.map((issue) => (
              <Link
                key={issue.id}
                href={`/issues/${issue.id}`}
                className="card card-hover p-3.5 flex items-center justify-between gap-3"
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
          <h2 className="font-semibold text-sm">
            Issues &amp; fixes
            <span className="ml-2 num text-xs text-lo">
              {visibleIssues.length}
              {visibleIssues.length !== issues.length && ` of ${issues.length}`}
            </span>
          </h2>
          <div className="flex gap-1 text-xs">
            {STATUS_FILTERS.map((f) => (
              <button
                key={f.key}
                onClick={() => setFilter(f.key)}
                className="px-2.5 py-1 rounded-md border transition"
                style={
                  filter === f.key
                    ? { borderColor: "var(--amber-dim)", background: "rgba(255,178,36,0.1)", color: "var(--amber)" }
                    : { borderColor: "var(--ink-700)", color: "var(--text-lo)" }
                }
              >
                {f.label}
              </button>
            ))}
          </div>
        </div>

        <div className="panel overflow-hidden">
          <table className="w-full text-sm responsive-table">
            <thead className="text-left" style={{ background: "var(--ink-800)" }}>
              <tr>
                <th className="px-3.5 py-2.5 section-label">Title</th>
                <th className="px-3.5 py-2.5 section-label">Origin</th>
                <th className="px-3.5 py-2.5 section-label">Category</th>
                <th className="px-3.5 py-2.5 section-label">Assurance</th>
                <th className="px-3.5 py-2.5 section-label">Status</th>
                <th className="px-3.5 py-2.5"></th>
              </tr>
            </thead>
            <tbody>
              {visibleIssues.map((issue) => (
                <tr key={issue.id} className="hairline transition hover:bg-white/[0.035]">
                  <td data-label="Title" className="px-3.5 py-2.5">
                    <Link href={`/issues/${issue.id}`} className="block hover:text-accent transition">
                      {issue.title}
                    </Link>
                  </td>
                  <td data-label="Origin" className="px-3.5 py-2.5">
                    <span className={`badge ${issue.origin === "detected" ? "badge-violet" : "badge-gray"}`}>
                      {issue.origin === "detected" ? "AI-detected" : "Filed externally"}
                    </span>
                  </td>
                  <td data-label="Category" className="px-3.5 py-2.5 text-mid">{issue.category}</td>
                  <td data-label="Assurance" className="px-3.5 py-2.5 num text-mid">{issue.assurance_score ?? "—"}</td>
                  <td data-label="Status" className="px-3.5 py-2.5">
                    <StatusBadge label={issue.badge} color={issue.color} />
                  </td>
                  <td className="px-3.5 py-2.5 text-right">
                    {issue.status === "raised" && (
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          resolveExternally(issue.id);
                        }}
                        className="btn btn-ghost px-2.5 py-1"
                      >
                        Resolve this
                      </button>
                    )}
                  </td>
                </tr>
              ))}
              {visibleIssues.length === 0 && (
                <tr>
                  <td colSpan={6} className="px-3 py-10 text-center text-sm text-lo">
                    {issues.length === 0
                      ? "No issues yet — run a scan to find some."
                      : `Nothing in "${activeFilter.label}" right now.`}
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

const TONE_BAR: Record<string, string> = {
  violet: "var(--pending)",
  green: "var(--verified)",
  amber: "var(--amber)",
  red: "var(--failed)",
};

function StatCard({ label, value, tone }: { label: string; value?: number; tone: string }) {
  return (
    <div className="card p-4 relative overflow-hidden">
      {/* A colour-coded edge rather than a coloured pill: the number is the
          content, and the tone belongs to the card, not to a second label. */}
      <span className="absolute left-0 top-0 bottom-0 w-[2px]" style={{ background: TONE_BAR[tone] }} aria-hidden />
      <div className="num text-2xl font-semibold tracking-tight">
        {value ?? <span className="skeleton inline-block h-6 w-8 align-middle" />}
      </div>
      <div className="mt-1.5 text-xs text-mid">{label}</div>
    </div>
  );
}
