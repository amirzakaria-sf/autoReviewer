"use client";

import { useEffect, useState, use } from "react";
import Link from "next/link";
import { api, type IssueSummary, type FixSummary } from "@/lib/api";
import { StatusBadge } from "@/components/StatusBadge";

export default function IssueDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [issue, setIssue] = useState<(IssueSummary & { fixes: FixSummary[] }) | null>(null);
  const [acting, setActing] = useState(false);

  async function refresh() {
    setIssue(await api.issue(id));
  }

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 4000);
    return () => clearInterval(interval);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  if (!issue) return <div className="text-gray-500">Loading…</div>;

  const fix = issue.fixes[0];

  async function act(action: "approve" | "reject") {
    if (!fix) return;
    setActing(true);
    await (action === "approve" ? api.approveFix(fix.id) : api.rejectFix(fix.id));
    await refresh();
    setActing(false);
  }

  return (
    <div className="space-y-6">
      <Link href="/dashboard" className="text-sm text-gray-400 hover:text-white">← back to overview</Link>

      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-xl font-semibold">{issue.title}</h1>
          <div className="mt-2 flex items-center gap-2 text-sm text-gray-400">
            <StatusBadge label={issue.badge} color={issue.color} />
            <span className={`badge ${issue.origin === "detected" ? "badge-blue" : "badge-gray"}`}>
              {issue.origin === "detected" ? "🤖 AI-detected" : "👤 filed externally"}
            </span>
            {issue.github_issue_number && (
              <a
                className="underline hover:text-white"
                target="_blank"
                href={`https://github.com/amirzakaria-sf/whipguard-demo-ui/issues/${issue.github_issue_number}`}
              >
                GitHub #{issue.github_issue_number}
              </a>
            )}
          </div>
        </div>
      </div>

      <section className="border border-border bg-panel rounded-lg p-4">
        <h2 className="font-semibold mb-2">Assurance rubric — score {issue.assurance_score ?? "—"}/100</h2>
        <p className="text-sm text-gray-400 mb-3">{issue.assurance_rubric?.verdict}</p>
        <div className="space-y-2">
          {issue.assurance_rubric?.factors?.map((f, i) => (
            <div key={i} className="flex items-start justify-between text-sm border-t border-border pt-2">
              <div>
                <div className="font-medium">{f.factor}</div>
                <div className="text-gray-500">{f.note}</div>
              </div>
              <div className="text-gray-300 font-mono">{f.weight}</div>
            </div>
          ))}
        </div>
      </section>

      {issue.evidence && (
        <section className="border border-border bg-panel rounded-lg p-4">
          <h2 className="font-semibold mb-2">Mechanical evidence</h2>
          <pre className="text-xs bg-black/40 rounded p-3 overflow-x-auto whitespace-pre-wrap">
            {String((issue.evidence as any).assertion_text ?? JSON.stringify(issue.evidence, null, 2))}
          </pre>
        </section>
      )}

      {fix && (
        <section className="border border-border bg-panel rounded-lg p-4 space-y-3">
          <div className="flex items-center justify-between">
            <h2 className="font-semibold">Proposed fix</h2>
            <StatusBadge label={fix.badge} color={fix.color} />
          </div>

          <p className="text-sm text-gray-400">
            Resolution score: {fix.resolution_score ?? "—"}/100 — {fix.resolution_rubric?.verdict}
          </p>

          <div className="flex flex-wrap gap-3 text-sm">
            {fix.pr_number && (
              <a
                className="underline hover:text-white"
                target="_blank"
                href={`https://github.com/amirzakaria-sf/whipguard-demo-ui/pull/${fix.pr_number}`}
              >
                PR #{fix.pr_number}
              </a>
            )}
            {fix.preview_url && (
              <a className="underline hover:text-white" target="_blank" href={fix.preview_url}>
                Live preview
              </a>
            )}
          </div>

          {fix.status === "awaiting-approval" && (
            <div className="flex gap-2 pt-2">
              <button
                disabled={acting}
                onClick={() => act("approve")}
                className="px-4 py-1.5 rounded-md bg-emerald-600 hover:bg-emerald-500 text-sm font-medium disabled:opacity-50"
              >
                Approve
              </button>
              <button
                disabled={acting}
                onClick={() => act("reject")}
                className="px-4 py-1.5 rounded-md bg-red-600/80 hover:bg-red-500 text-sm font-medium disabled:opacity-50"
              >
                Reject
              </button>
            </div>
          )}

          {fix.approved_by && (
            <p className="text-xs text-gray-500">
              Handled by {fix.approved_by} via {fix.approved_via}
            </p>
          )}
        </section>
      )}

      {fix?.outcome_check && (
        <section
          className={`border rounded-lg p-4 space-y-3 ${
            fix.outcome_check.agreed ? "border-emerald-800 bg-emerald-950/30" : "border-red-800 bg-red-950/30"
          }`}
        >
          <div className="flex items-center justify-between">
            <h2 className="font-semibold">Cross-app outcome check</h2>
            <StatusBadge
              label={fix.outcome_check.agreed ? "All systems agree" : "Outcome check failed"}
              color={fix.outcome_check.agreed ? "green" : "red"}
            />
          </div>
          <p className="text-xs text-gray-500">
            Independently reads GitHub, Cloudflare, Slack, and this dashboard back after the run and
            reduces them to one status. Any disagreement fails the whole run closed, even if every
            step above reported success.
          </p>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-xs">
            {(["github_state", "cloudflare_state", "slack_state"] as const).map((key) => (
              <div key={key} className="bg-black/30 rounded p-2">
                <div className="text-gray-500 mb-1">{key.replace("_state", "")}</div>
                <pre className="whitespace-pre-wrap break-words">{JSON.stringify(fix.outcome_check![key], null, 1)}</pre>
              </div>
            ))}
            <div className="bg-black/30 rounded p-2">
              <div className="text-gray-500 mb-1">dashboard</div>
              <pre>{fix.outcome_check.dashboard_state}</pre>
            </div>
          </div>
          {fix.outcome_check.mismatch_detail && (
            <div className="text-xs text-red-300">
              Mismatch: {JSON.stringify(fix.outcome_check.mismatch_detail)}
            </div>
          )}
        </section>
      )}
    </div>
  );
}
