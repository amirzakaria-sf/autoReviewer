"use client";

import { useCallback, useEffect, useState, use } from "react";
import Link from "next/link";
import { api, type IssueDetail } from "@/lib/api";
import { StatusBadge } from "@/components/StatusBadge";
import { ScoreRing } from "@/components/ScoreRing";
import { RowSkeleton } from "@/components/EmptyState";
import { FixReviewPanel } from "@/components/FixReviewPanel";

function githubUrl(repo: string | null | undefined, kind: "issues" | "pull", number: number) {
  return repo ? `https://github.com/${repo}/${kind}/${number}` : undefined;
}

export default function IssueDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [issue, setIssue] = useState<IssueDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setIssue(await api.issue(id));
      setError(null);
    } catch {
      setError("Could not load this issue.");
    }
  }, [id]);

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 4000);
    return () => clearInterval(interval);
  }, [refresh]);

  if (error && !issue) return <p className="text-sm text-failed">{error}</p>;
  if (!issue) return <RowSkeleton rows={3} />;

  const fix = issue.fixes[0];

  const evidenceText =
    issue.evidence &&
    String(
      (issue.evidence as Record<string, unknown>).assertion_text ?? JSON.stringify(issue.evidence, null, 2),
    );

  // pb-20 clears the fixed Ask Counsel launcher, which otherwise sits on top
  // of the last thing on the page -- on a long review thread, the most recent
  // message.
  return (
    <div className="space-y-6 animate-fade-in pb-20">
      <Link href="/dashboard" className="text-xs text-lo hover:text-hi transition">
        ← Overview
      </Link>

      <header className="flex items-start gap-5">
        <ScoreRing score={issue.assurance_score} threshold={issue.assurance_threshold ?? undefined} label="assurance confidence" />
        <div className="min-w-0 flex-1">
          <h1 className="text-lg font-semibold leading-snug">{issue.title}</h1>
          <div className="mt-2.5 flex flex-wrap items-center gap-2">
            <StatusBadge label={issue.badge} color={issue.color} />
            <span className={`badge ${issue.origin === "detected" ? "badge-violet" : "badge-gray"}`}>
              {issue.origin === "detected" ? "AI-detected" : "Filed externally"}
            </span>
            <span className="badge badge-gray">{issue.category}</span>
            {issue.github_issue_number && issue.repo_full_name && (
              <a
                className="badge badge-gray hover:text-hi transition"
                target="_blank"
                rel="noreferrer"
                href={githubUrl(issue.repo_full_name, "issues", issue.github_issue_number)}
              >
                GitHub #{issue.github_issue_number}
              </a>
            )}
          </div>
          {issue.repo_full_name && <p className="mt-2 text-xs text-lo num">{issue.repo_full_name}</p>}
        </div>
      </header>

      <section className="card p-5">
        <div className="flex items-center justify-between mb-1">
          <h2 className="section-label">Assurance rubric</h2>
          {issue.assurance_threshold !== null && issue.assurance_threshold !== undefined && (
            <span className="text-[11px] text-lo">
              threshold <span className="num text-mid">{issue.assurance_threshold}</span>
            </span>
          )}
        </div>
        <p className="text-sm text-mid mb-4 leading-relaxed">{issue.assurance_rubric?.verdict ?? "No verdict recorded."}</p>
        <div>
          {issue.assurance_rubric?.factors?.map((factor, index) => (
            <div key={index} className="flex items-start justify-between gap-4 py-2.5 hairline first:border-t-0">
              <div className="min-w-0">
                <div className="text-sm font-medium">{factor.factor}</div>
                <div className="text-xs text-lo mt-0.5 leading-relaxed">{factor.note}</div>
              </div>
              <div className="num text-sm text-mid shrink-0">{factor.weight}</div>
            </div>
          ))}
          {!issue.assurance_rubric?.factors?.length && (
            <p className="text-xs text-lo">No factors recorded — this finding did not reach a jury.</p>
          )}
        </div>
      </section>

      {evidenceText && (
        <section className="card p-5">
          <h2 className="section-label mb-3">Mechanical evidence</h2>
          <pre
            className="text-[11px] leading-relaxed rounded-lg p-3.5 overflow-x-auto whitespace-pre-wrap num"
            style={{ background: "var(--ink-900)", border: "1px solid var(--ink-700)", maxHeight: 340 }}
          >
            {evidenceText}
          </pre>
        </section>
      )}

      {/* The fix, its diff, the review conversation, and the three verbs.
          Approve/reject/revise all live in here now -- the old inline pair of
          buttons could only carry a boolean, and a boolean cannot say what to
          do differently. */}
      <FixReviewPanel issueId={id} onChanged={refresh} />

      {fix?.approved_by && (
        <p className="text-xs text-lo">
          Handled by <span className="text-mid">{fix.approved_by}</span> via {fix.approved_via}
        </p>
      )}

      {fix?.outcome_check && (
        <section
          className="card p-5 space-y-3"
          style={{ borderColor: fix.outcome_check.agreed ? "var(--verified-dim)" : "var(--failed-dim)" }}
        >
          <div className="flex items-center justify-between">
            <h2 className="section-label">Cross-app outcome check</h2>
            <StatusBadge
              label={fix.outcome_check.agreed ? "All systems agree" : "Outcome check failed"}
              color={fix.outcome_check.agreed ? "green" : "red"}
            />
          </div>
          <p className="text-xs text-lo leading-relaxed">
            Independently reads GitHub, Cloudflare, Slack and this dashboard back after the run and reduces
            them to one status. Any disagreement fails the whole run closed, even if every step above
            reported success.
          </p>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
            {(["github_state", "cloudflare_state", "slack_state"] as const).map((key) => (
              <div key={key} className="rounded-lg p-2.5" style={{ background: "var(--ink-900)", border: "1px solid var(--ink-700)" }}>
                <div className="section-label mb-1.5">{key.replace("_state", "")}</div>
                <pre className="text-[10px] whitespace-pre-wrap break-words num text-mid">
                  {JSON.stringify(fix.outcome_check![key], null, 1)}
                </pre>
              </div>
            ))}
            <div className="rounded-lg p-2.5" style={{ background: "var(--ink-900)", border: "1px solid var(--ink-700)" }}>
              <div className="section-label mb-1.5">dashboard</div>
              <pre className="text-[10px] num text-mid">{fix.outcome_check.dashboard_state}</pre>
            </div>
          </div>
          {fix.outcome_check.mismatch_detail && (
            <p className="text-xs" style={{ color: "#ff8b84" }}>
              Mismatch: {JSON.stringify(fix.outcome_check.mismatch_detail)}
            </p>
          )}
        </section>
      )}
    </div>
  );
}
