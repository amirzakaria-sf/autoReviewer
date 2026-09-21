"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Icon } from "@/components/Icon";
import { api, ReviewConflictError, type FixReview, type ReviewTurn } from "@/lib/api";
import { DiffView } from "@/components/DiffView";
import { ScoreRing } from "@/components/ScoreRing";
import { StatusBadge } from "@/components/StatusBadge";
import { useToast } from "@/components/Toast";

/** While an attempt is being reworked the council is doing real work -- a
 *  retrieval pass, a patch, a sandbox run and an adversarial score -- so the
 *  page polls faster than its usual idle cadence and says what is happening
 *  rather than sitting on a spinner. */
const REVISING_POLL_MS = 2500;
const IDLE_POLL_MS = 8000;

const WORKING_STEPS = [
  "Re-reading the code around the finding",
  "Drafting a different patch",
  "Running the same check in a sandbox",
  "Scoring it against the rubric",
];

function timeLabel(at: string) {
  try {
    return new Date(at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  } catch {
    return "";
  }
}

function Turn({ turn }: { turn: ReviewTurn }) {
  const human = turn.role === "human";
  const isRevision = turn.kind === "revision-request";
  const isDecision = turn.kind?.startsWith("decision:");

  return (
    <div className={`flex gap-3 ${human ? "flex-row-reverse" : ""}`}>
      <div
        className="w-7 h-7 rounded-lg shrink-0 flex items-center justify-center text-[11px]"
        style={{
          background: human ? "var(--accent-dim)" : "var(--ink-800)",
          border: "1px solid var(--ink-700)",
        }}
        aria-hidden
      >
        <Icon name={human ? "approve" : "scales"} size={16} />
      </div>
      <div className={`min-w-0 max-w-[85%] ${human ? "text-right" : ""}`}>
        <div className="flex items-center gap-2 mb-1" style={{ justifyContent: human ? "flex-end" : "flex-start" }}>
          <span className="section-label">{human ? "You" : "Fix Council"}</span>
          {isRevision && <span className="badge badge-amber">asked for changes</span>}
          {isDecision && (
            <span className={`badge ${turn.kind === "decision:approved" ? "badge-green" : "badge-gray"}`}>
              {turn.kind === "decision:approved" ? "approved" : "rejected"}
            </span>
          )}
          <span className="text-[10px] text-lo num">{timeLabel(turn.at)}</span>
        </div>
        <div
          className="rounded-lg px-3.5 py-2.5 text-sm leading-relaxed whitespace-pre-wrap text-left"
          style={{
            background: human ? "var(--accent-dim)" : "var(--ink-900)",
            border: "1px solid var(--ink-700)",
            color: "var(--text-mid)",
          }}
        >
          {turn.text}
        </div>
      </div>
    </div>
  );
}

function WorkingTrail() {
  const [step, setStep] = useState(0);

  useEffect(() => {
    // Deliberately not a progress bar: nothing here reports real percentage,
    // and a bar that invents one is a lie the user learns not to trust. This
    // only claims that work is moving and names the kind of work it is.
    const interval = setInterval(() => setStep((current) => (current + 1) % WORKING_STEPS.length), 2600);
    return () => clearInterval(interval);
  }, []);

  return (
    <div className="flex gap-3">
      <div
        className="w-7 h-7 rounded-lg shrink-0 flex items-center justify-center"
        style={{ background: "var(--ink-800)", border: "1px solid var(--ink-700)" }}
        aria-hidden
      >
        <span className="spinner" />
      </div>
      <div className="min-w-0">
        <div className="section-label mb-1">Fix Council</div>
        <div
          className="rounded-lg px-3.5 py-2.5"
          style={{ background: "var(--ink-900)", border: "1px solid var(--ink-700)" }}
        >
          {WORKING_STEPS.map((label, index) => (
            <div
              key={label}
              className="flex items-center gap-2 text-sm py-0.5"
              style={{ color: index <= step ? "var(--text-mid)" : "var(--text-lo)", opacity: index <= step ? 1 : 0.5 }}
            >
              <span aria-hidden>{index < step ? "✓" : index === step ? "→" : "·"}</span>
              <span>{label}</span>
              {index === step && <span className="caret" aria-hidden />}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

export function FixReviewPanel({ issueId, onChanged }: { issueId: string; onChanged?: () => void }) {
  const [review, setReview] = useState<FixReview | null>(null);
  const [instruction, setInstruction] = useState("");
  const [busy, setBusy] = useState<"approve" | "reject" | "revise" | null>(null);
  const [rejecting, setRejecting] = useState(false);
  const threadRef = useRef<HTMLDivElement | null>(null);
  const seenTurns = useRef<number | null>(null);
  const toast = useToast();

  const refresh = useCallback(async () => {
    try {
      setReview(await api.fixReview(issueId));
    } catch {
      // The panel is additive: the page around it still renders the finding,
      // so a failed poll stays quiet rather than replacing the page with an
      // error the user can do nothing about.
    }
  }, [issueId]);

  const revising = review?.status === "revising";

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, revising ? REVISING_POLL_MS : IDLE_POLL_MS);
    return () => clearInterval(interval);
  }, [refresh, revising]);

  useEffect(() => {
    const thread = threadRef.current;
    const turns = review?.transcript.length ?? 0;
    if (!thread) return;

    // Scroll the THREAD, not the page. `scrollIntoView` on an element inside a
    // nested scroller moves the document too, which dropped the reader into
    // the middle of the opening message on first load.
    const previous = seenTurns.current;
    const first = previous === null;
    seenTurns.current = turns;

    // On first render the newest turn is the one worth seeing, so jump there
    // without animating. Afterwards only move when a turn actually arrived --
    // scrolling someone away from what they are reading, on a poll that
    // changed nothing, is worse than leaving them where they are. Compare
    // against the PREVIOUS count, captured above: reading it back after the
    // assignment makes the check trivially false.
    if (first || turns !== previous) {
      thread.scrollTo({ top: thread.scrollHeight, behavior: first ? "auto" : "smooth" });
    }
  }, [review?.transcript.length, revising]);

  if (!review || !review.current) return null;

  const fix = review.current;
  const open = fix.status === "awaiting-approval";

  async function run(action: "approve" | "reject" | "revise") {
    if (!review?.current) return;
    const id = review.current.id;
    setBusy(action);
    try {
      if (action === "revise") {
        await api.reviseFix(id, instruction.trim());
        setInstruction("");
        toast("info", "Sent back to the Fix Council with your notes.");
      } else if (action === "approve") {
        await api.approveFix(id);
        toast("success", "Approved — pushing the branch, opening the PR and deploying.");
      } else {
        await api.rejectFix(id, instruction.trim());
        setInstruction("");
        setRejecting(false);
        toast("info", "Rejected. Nothing was pushed, and the issue is open again.");
      }
      await refresh();
      onChanged?.();
    } catch (error) {
      if (error instanceof ReviewConflictError) {
        toast("info", error.message);
        await refresh();
      } else {
        toast("error", `Could not ${action} this fix. Nothing was changed.`);
      }
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="card p-5 space-y-5">
      <div className="flex items-start gap-5">
        <ScoreRing score={fix.score} size={60} label="resolution confidence" />
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-3">
            <h2 className="font-semibold text-sm">
              Proposed fix
              {fix.attempt > 1 && <span className="text-lo font-normal"> · attempt {fix.attempt}</span>}
            </h2>
            <StatusBadge label={fix.badge} color={fix.color} />
          </div>
          <p className="text-sm text-mid mt-2 leading-relaxed">{fix.verdict ?? "No verdict recorded."}</p>
          <div className="mt-3 flex flex-wrap gap-2">
            {fix.branch_name && <span className="badge badge-gray num">{fix.branch_name}</span>}
            {fix.pr_number ? (
              <span className="badge badge-gray num">PR #{fix.pr_number}</span>
            ) : (
              <span className="badge badge-gray">no branch pushed yet</span>
            )}
            {review.history.length > 0 && (
              <span className="badge badge-gray">
                {review.history.length} earlier attempt{review.history.length === 1 ? "" : "s"}
              </span>
            )}
          </div>
        </div>
      </div>

      <DiffView diff={fix.diff} />

      {(review.transcript.length > 0 || revising) && (
        <div className="space-y-4 pt-1">
          <div className="section-label">Review</div>
          <div ref={threadRef} className="space-y-4 max-h-[420px] overflow-y-auto pr-1">
            {review.transcript.map((turn, index) => (
              <Turn key={index} turn={turn} />
            ))}
            {revising && <WorkingTrail />}
            {/* Clears the fixed Ask Counsel launcher, which floats over the
                bottom-right of the viewport and otherwise covers whichever
                message happens to be there. */}
            <div className="h-10" aria-hidden />
          </div>
        </div>
      )}

      {open && (
        <div className="space-y-3 pt-1">
          <textarea
            id="review-instruction"
            value={instruction}
            onChange={(event) => setInstruction(event.target.value)}
            rows={3}
            placeholder={
              rejecting
                ? "Why is this wrong? (optional, but a later attempt reads it back)"
                : "Want a different approach? Say what to do instead — e.g. “use the existing formatCurrency helper rather than adding a dependency”."
            }
            className="w-full rounded-lg px-3.5 py-2.5 text-sm leading-relaxed resize-y"
            style={{
              background: "var(--ink-900)",
              border: "1px solid var(--ink-700)",
              color: "var(--text-hi)",
            }}
          />
          <div className="flex flex-wrap gap-3">
            <button
              disabled={busy !== null}
              onClick={() => run("approve")}
              className="btn btn-approve flex-1 w-full sm:w-auto sm:min-w-[160px] px-5 py-2.5"
            >
              {busy === "approve" ? "Working…" : "Approve & build"}
            </button>
            <button
              disabled={busy !== null || !instruction.trim() || review.revisions_left <= 0}
              onClick={() => run("revise")}
              className="btn btn-ghost flex-1 w-full sm:w-auto sm:min-w-[160px] px-5 py-2.5"
              title={
                review.revisions_left <= 0
                  ? "This fix has been reworked as many times as the limit allows."
                  : "Send it back with your notes"
              }
            >
              {busy === "revise" ? "Sending…" : "Ask for changes"}
            </button>
            <button
              disabled={busy !== null}
              onClick={() => (rejecting ? run("reject") : setRejecting(true))}
              className="btn btn-reject flex-1 w-full sm:w-auto sm:min-w-[120px] px-5 py-2.5"
            >
              {busy === "reject" ? "Working…" : rejecting ? "Confirm reject" : "Reject"}
            </button>
          </div>
          <p className="text-[11px] text-lo leading-relaxed">
            Nothing is pushed to GitHub until you approve — rejecting leaves the repository untouched and
            reopens the issue.{" "}
            {review.revisions_left > 0
              ? `${review.revisions_left} rework${review.revisions_left === 1 ? "" : "s"} left.`
              : "No reworks left on this issue."}
          </p>
        </div>
      )}

      {fix.decision_note && !open && (
        <p className="text-xs text-lo">
          Noted: <span className="text-mid">{fix.decision_note}</span>
        </p>
      )}
    </section>
  );
}
