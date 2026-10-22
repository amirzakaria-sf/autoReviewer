"use client";

import { useEffect, useRef, useState } from "react";
import { Icon, type IconName } from "@/components/Icon";

/* The six moves, with one of them always "live". It advances on its own
 * while the section is on screen and stops for good the moment a visitor
 * picks one: an auto-advance that keeps moving under someone's cursor is
 * taking the page away from them. */

const STEPS: { icon: IconName; title: string; short: string; body: string; detail: string[] }[] = [
  {
    icon: "search",
    title: "Detect",
    short: "A real check fails",
    body: "Every push runs the detector for each category you enabled: a browser test, a unit test run, a secret scan, a bundle budget, an accessibility audit, a docs drift check.",
    detail: ["Runs in a throwaway sandbox, never on the web server", "A failure is evidence, not an opinion", "The same check is reused later to judge the fix"],
  },
  {
    icon: "scales",
    title: "Argue",
    short: "Two sides, one Arbiter",
    body: "A Skeptic argues it is not a bug. A Corroborator argues it is. The detector runs again on a clean checkout, and an Arbiter scores the finding against a written rubric.",
    detail: ["Disagreement over 30 points calls a Meta-Auditor", "Each category has its own bar to clear", "Missing product intent? It asks you instead of guessing"],
  },
  {
    icon: "merge",
    title: "Patch",
    short: "Edits scoped to its lane",
    body: "A patch worker reads the code and edits it with exact find-and-replace patches. Each category has a write scope: a UI fix can never touch the test that will judge it.",
    detail: ["Grounded in lexical search and a symbol graph of your repo", "A loop tripwire stops a worker repeating itself", "Rejected attempts become context for the next one"],
  },
  {
    icon: "check",
    title: "Verify",
    short: "The same check, now passing",
    body: "The detector that caught the bug runs on the patched branch. A second skeptic looks for regressions, and an Arbiter scores the fix against a resolution bar.",
    detail: ["Verified before anyone is asked to look", "Fails closed: a broken check is not a pass", "Every score breaks down into weighted factors"],
  },
  {
    icon: "approve",
    title: "You decide",
    short: "Approve, ask, or reject",
    body: "You get the diff, the evidence and the scores on the dashboard, in Slack, by email or as a push notification. Three verbs: approve, ask for changes, reject.",
    detail: ["Nothing reaches GitHub before you say yes", "Ask for changes and it tries again with your note", "Reject and the repository is untouched"],
  },
  {
    icon: "deploy",
    title: "Prove it live",
    short: "Preview, re-check, your merge",
    body: "Approval pushes a branch, opens a pull request and deploys a preview. The check runs again against the live preview URL, then GitHub, the deploy, Slack and the dashboard are read back independently.",
    detail: ["Any disagreement fails the run closed", "WhipGuard has no merge capability", "Merge it yourself and the branch is cleaned up"],
  },
];

export function PipelineSteps() {
  const [active, setActive] = useState(0);
  const [pinned, setPinned] = useState(false);
  const [visible, setVisible] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const node = ref.current;
    if (!node || typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver(([entry]) => setVisible(entry.isIntersecting), { threshold: 0.35 });
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (pinned || !visible) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const timer = window.setInterval(() => setActive((i) => (i + 1) % STEPS.length), 3200);
    return () => window.clearInterval(timer);
  }, [pinned, visible]);

  const step = STEPS[active];

  return (
    <div ref={ref} className="lp-pipe">
      <div className="lp-pipe-rail" role="tablist" aria-label="Pipeline steps">
        <div className="lp-pipe-beam" style={{ width: `${(active / (STEPS.length - 1)) * 100}%` }} aria-hidden />
        {STEPS.map((s, i) => (
          <button
            key={s.title}
            type="button"
            role="tab"
            aria-selected={i === active}
            className={`lp-pipe-node ${i === active ? "on" : ""} ${i < active ? "past" : ""}`}
            onClick={() => {
              setActive(i);
              setPinned(true);
            }}
          >
            <span className="lp-pipe-dot">
              <Icon name={s.icon} size={16} />
            </span>
            <span className="lp-pipe-t">
              <span className="num text-[10px] text-lo">{String(i + 1).padStart(2, "0")}</span>
              <span className="block text-[13px] font-medium">{s.title}</span>
              <span className="block text-[11px] text-lo">{s.short}</span>
            </span>
          </button>
        ))}
      </div>

      <div key={step.title} className="lp-pipe-body card animate-fade-in" role="tabpanel">
        <div className="lp-pipe-ic">
          <Icon name={step.icon} size={22} />
        </div>
        <div className="min-w-0">
          <h3 className="text-lg font-semibold">{step.title}</h3>
          <p className="text-sm text-mid mt-1.5 leading-relaxed">{step.body}</p>
          <ul className="lp-ticks mt-3">
            {step.detail.map((d) => (
              <li key={d}>{d}</li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  );
}
