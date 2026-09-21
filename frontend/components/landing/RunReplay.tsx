"use client";

import { useEffect, useRef, useState } from "react";
import { Icon, type IconName } from "@/components/Icon";

/**
 * The landing page's one piece of evidence.
 *
 * Everything above it is a claim — "an adversarial jury", "verified twice",
 * "nothing ships without a yes" — and a claim about a multi-agent system
 * reads exactly like any other marketing sentence. This replays the shape of
 * a real run instead: a detector that fails, two jurors who disagree, an
 * Arbiter that scores, a patch whose FIRST attempt is rejected by the
 * verifier, and a fix that ends up waiting on a human.
 *
 * It is a REPLAY, not a live connection, and it must stay one. This page is
 * served to signed-out visitors, so a widget that needed a session would
 * either be empty or be showing somebody else's repository.
 *
 * The rejection is in it deliberately. A demo where every step passes is the
 * one a reader discounts, and the gates are the part of this product that is
 * genuinely unlike an LLM in a text box — watching one catch something IS the
 * argument. The numbers are the ones a real run produced.
 */

type Line = {
  id: string;
  icon: IconName;
  tone: "neutral" | "amber" | "violet" | "green" | "red";
  label: string;
  meta?: string;
  /** Rendered underneath, indented — a reason, a citation, a real error. */
  note?: string;
};

const SCRIPT: Line[] = [
  { id: "detect", icon: "search", tone: "neutral", label: "backend detector runs", meta: "node --test",
    note: "calculateTotal ignores item.quantity — 1 assertion failed" },
  { id: "skeptic", icon: "scales", tone: "violet", label: "Skeptic · argues it is not a bug", meta: "confidence 20" },
  { id: "corrob", icon: "scales", tone: "violet", label: "Corroborator · argues it is", meta: "confidence 94" },
  { id: "arbiter", icon: "check", tone: "amber", label: "Arbiter scores the finding", meta: "assurance 92 / 100",
    note: "above this category's threshold of 75 — issue raised on GitHub" },
  { id: "patch", icon: "merge", tone: "neutral", label: "patch worker · apply_patch", meta: "reasoning on",
    note: "items.reduce((sum, i) => sum + i.price * i.quantity, 0)" },
  { id: "reject", icon: "halt", tone: "red", label: "verifier rejected attempt 1",
    note: "the category's own detector still fails on the patched branch" },
  { id: "retry", icon: "loop", tone: "neutral", label: "patch worker · attempt 2", meta: "prior rejection as context" },
  { id: "verify", icon: "check", tone: "green", label: "verifier · same detector, patched branch", meta: "passes" },
  { id: "score", icon: "chart", tone: "amber", label: "Arbiter scores the fix", meta: "resolution 100 / 100",
    note: "root-cause correction 55 · verification 30 · scope preserved 15" },
  { id: "wait", icon: "approve", tone: "green", label: "waiting for a human", meta: "nothing reached main" },
];

const TONE: Record<Line["tone"], string> = {
  neutral: "var(--text-mid)",
  amber: "var(--amber)",
  violet: "var(--pending)",
  green: "var(--verified)",
  red: "var(--failed)",
};

const STEP_MS = 760;

export function RunReplay() {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [reduceMotion, setReduceMotion] = useState(false);
  const [started, setStarted] = useState(false);
  const [shown, setShown] = useState(0);

  // Read the preference on the client only: matchMedia does not exist during
  // the server render, and guessing wrong means either a flash of animation
  // for someone who asked for none, or no content for everyone.
  useEffect(() => {
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    if (query.matches) {
      setReduceMotion(true);
      setShown(SCRIPT.length);
    }
  }, []);

  // Only plays once it is actually on screen. A replay that finishes below
  // the fold is a replay nobody saw — and on a phone this section is several
  // screens down.
  useEffect(() => {
    if (reduceMotion || started) return;
    const node = containerRef.current;
    if (!node) return;
    if (typeof IntersectionObserver === "undefined") {
      setStarted(true);
      return;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          setStarted(true);
          observer.disconnect();
        }
      },
      { threshold: 0.3 },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, [reduceMotion, started]);

  useEffect(() => {
    if (!started || reduceMotion || shown >= SCRIPT.length) return;
    const timer = window.setTimeout(() => setShown((count) => count + 1), STEP_MS);
    return () => window.clearTimeout(timer);
  }, [started, shown, reduceMotion]);

  const done = shown >= SCRIPT.length;

  return (
    <div ref={containerRef} className="card overflow-hidden text-left">
      <div
        className="flex items-center justify-between gap-3 px-4 py-2.5"
        style={{ borderBottom: "1px solid var(--ink-700)" }}
      >
        <span className="section-label">A run, replayed</span>
        <span className="inline-flex items-center gap-1.5 num text-[10px] text-lo">
          <span
            className={`dot ${done ? "" : "animate-pulse-dot"}`}
            style={{ background: done ? "var(--verified)" : "var(--amber)" }}
          />
          {done ? "finished" : "working"}
        </span>
      </div>

      {/* It is a list of steps, so it is an ordered list. The animation is
          applied to the items rather than the whole thing being a picture. */}
      <ol>
        {SCRIPT.slice(0, shown).map((line, index) => (
          <li
            key={line.id}
            className="flex items-start gap-3 px-4 py-2.5 animate-fade-in"
            style={{ borderTop: index === 0 ? "none" : "1px solid var(--ink-800)" }}
          >
            <span className="mt-0.5 shrink-0" style={{ color: TONE[line.tone] }}>
              <Icon name={line.icon} size={14} strokeWidth={1.7} />
            </span>
            <span className="min-w-0 flex-1">
              <span className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
                <span className="text-[13px] leading-5" style={{ color: TONE[line.tone] }}>
                  {line.label}
                </span>
                {line.meta && <span className="num text-[10px] text-lo">{line.meta}</span>}
              </span>
              {line.note && (
                <span className="mt-0.5 block num text-[11px] leading-5 text-lo break-anywhere">{line.note}</span>
              )}
            </span>
          </li>
        ))}

        {/* Placeholder rows for what has not played yet, so the panel keeps a
            fixed height instead of growing by a line every 760ms and shoving
            the rest of the page down while it is being read. */}
        {!done &&
          Array.from({ length: SCRIPT.length - shown }).map((_, index) => (
            <li
              key={`pending-${index}`}
              className="flex items-center gap-3 px-4 py-2.5"
              style={{ borderTop: "1px solid var(--ink-800)" }}
              aria-hidden
            >
              <span
                className="shrink-0 rounded-full"
                style={{ width: 14, height: 14, border: "1px solid var(--ink-700)" }}
              />
              <span className="h-2 flex-1 rounded" style={{ background: "var(--ink-800)" }} />
            </li>
          ))}
      </ol>
    </div>
  );
}
