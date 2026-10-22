"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Icon, type IconName } from "@/components/Icon";
import { CATEGORY, DISAGREEMENT_THRESHOLD, SCENARIOS, type Scenario } from "./sim-data";

/* The landing page's centrepiece: one bug, end to end, with the visitor in
 * the approver's seat.
 *
 * It replays the real control flow rather than a happy path. The jury can
 * disagree and summon the Meta-Auditor, the Arbiter can refuse to score and
 * ask a person instead, the verifier can reject a patch, and the three verbs
 * at the end do what the product's do: approve ships a branch and a preview
 * and then STOPS at an open pull request, ask-for-changes runs another
 * attempt, reject leaves the repository untouched. It is a script, not a
 * connection -- signed-out visitors must never see somebody's repository. */

type StageKey = "detect" | "jury" | "arbiter" | "patch" | "verify" | "decide" | "ship";
type StageState = "idle" | "active" | "done" | "fail" | "skip";

const STAGES: { key: StageKey; label: string; icon: IconName }[] = [
  { key: "detect", label: "Detect", icon: "search" },
  { key: "jury", label: "Jury", icon: "scales" },
  { key: "arbiter", label: "Arbiter", icon: "chart" },
  { key: "patch", label: "Patch", icon: "merge" },
  { key: "verify", label: "Verify", icon: "check" },
  { key: "decide", label: "Your call", icon: "approve" },
  { key: "ship", label: "Preview", icon: "deploy" },
];

type Tone = "mid" | "amber" | "violet" | "green" | "red";
type LogLine = { id: number; at: string; text: string; tone: Tone };
type Phase = "idle" | "running" | "question" | "decision" | "notes" | "done";

const TONE: Record<Tone, string> = {
  mid: "var(--text-mid)",
  amber: "var(--amber)",
  violet: "var(--pending)",
  green: "var(--verified)",
  red: "var(--failed)",
};

const NOTE_PRESETS = ["Add a regression test for this case", "Keep the change to one function", "Explain the root cause in the PR body"];

class Cancelled extends Error {}

function freshStages(): Record<StageKey, StageState> {
  return { detect: "idle", jury: "idle", arbiter: "idle", patch: "idle", verify: "idle", decide: "idle", ship: "idle" };
}

export function Simulator() {
  const [scenarioId, setScenarioId] = useState(SCENARIOS[0].id);
  const [failFirst, setFailFirst] = useState(false);
  const [falsePositive, setFalsePositive] = useState(false);
  const [ambiguous, setAmbiguous] = useState(false);

  const [phase, setPhase] = useState<Phase>("idle");
  const [stages, setStages] = useState(freshStages);
  const [evidence, setEvidence] = useState<string[]>([]);
  const [jury, setJury] = useState<{ skeptic: number; corroborator: number } | null>(null);
  const [meta, setMeta] = useState(false);
  const [score, setScore] = useState<number | null>(null);
  const [resolution, setResolution] = useState<number | null>(null);
  const [diffShown, setDiffShown] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [outcome, setOutcome] = useState<null | { tone: Tone; title: string; body: string }>(null);
  const [log, setLog] = useState<LogLine[]>([]);
  const [note, setNote] = useState("");

  const scenario = SCENARIOS.find((s) => s.id === scenarioId) ?? SCENARIOS[0];
  const category = CATEGORY[scenario.category];

  // Every run gets a token; a newer run (or leaving the page) cancels the
  // older one at its next await instead of letting two scripts interleave.
  const runToken = useRef(0);
  const clock = useRef(0);
  const logId = useRef(0);
  const logRef = useRef<HTMLOListElement | null>(null);
  const reduceMotion = useRef(false);

  useEffect(() => {
    reduceMotion.current = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    return () => {
      runToken.current += 1;
    };
  }, []);

  useEffect(() => {
    const node = logRef.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [log]);

  // On a phone the card that needs a decision sits two screens below the
  // button that started the run. Bring it into view when it starts waiting
  // on the visitor -- only if it is actually off screen, so a desktop
  // layout, where it is always visible, never jumps.
  const cardRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (phase !== "decision" && phase !== "question") return;
    const node = cardRef.current;
    if (!node) return;
    const box = node.getBoundingClientRect();
    if (box.top >= 0 && box.bottom <= window.innerHeight) return;
    node.scrollIntoView({ block: "center", behavior: reduceMotion.current ? "auto" : "smooth" });
  }, [phase]);

  const wait = useCallback(async (token: number, ms: number) => {
    await new Promise((resolve) => window.setTimeout(resolve, reduceMotion.current ? Math.min(ms, 60) : ms));
    if (token !== runToken.current) throw new Cancelled();
    clock.current += ms * 9; // compressed time: the log reads in "real" seconds
  }, []);

  const say = useCallback((text: string, tone: Tone = "mid") => {
    const seconds = Math.floor(clock.current / 1000);
    const at = `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
    logId.current += 1;
    const id = logId.current;
    setLog((lines) => [...lines.slice(-60), { id, at, text, tone }]);
  }, []);

  const stage = useCallback((key: StageKey, state: StageState) => {
    setStages((current) => ({ ...current, [key]: state }));
  }, []);

  function reset() {
    runToken.current += 1;
    clock.current = 0;
    setPhase("idle");
    setStages(freshStages());
    setEvidence([]);
    setJury(null);
    setMeta(false);
    setScore(null);
    setResolution(null);
    setDiffShown(false);
    setAttempt(0);
    setOutcome(null);
    setLog([]);
    setNote("");
  }

  async function patchAndVerify(token: number, s: Scenario, opts: { fail: boolean; reviewerNote?: string }) {
    stage("patch", "active");
    stage("verify", "idle");
    setResolution(null);
    let tries = 0;
    for (;;) {
      tries += 1;
      setAttempt((n) => n + 1);
      say(
        opts.reviewerNote && tries === 1
          ? `patch worker · new attempt with your note: "${opts.reviewerNote}"`
          : `patch worker · reads ${s.file}, calls apply_patch`,
        "mid",
      );
      await wait(token, 900);
      setDiffShown(true);
      stage("patch", "done");
      stage("verify", "active");
      say(`verifier · ${category.detector} on the patched branch, in a sandbox`, "mid");
      say("fix skeptic · argues the patch could break something else", "violet");
      await wait(token, 1000);
      if (opts.fail && tries === 1) {
        stage("verify", "fail");
        say(`verifier rejected: ${s.firstAttemptFailure}`, "red");
        await wait(token, 900);
        say("retrying with the rejection as context, not a blank slate", "amber");
        stage("patch", "active");
        await wait(token, 500);
        continue;
      }
      break;
    }
    say(`verifier · ${s.verify}`, "green");
    const total = s.resolution.reduce((sum, f) => sum + f.weight, 0);
    await wait(token, 600);
    setResolution(total);
    stage("verify", "done");
    say(`Arbiter · resolution ${total}/100 (needs ${category.resolution})`, "amber");
    await wait(token, 500);
    stage("decide", "active");
    say("waiting for a human. Nothing has left this sandbox.", "green");
    setPhase("decision");
  }

  async function play() {
    reset();
    const token = runToken.current;
    const s = scenario;
    setPhase("running");
    try {
      stage("detect", "active");
      say(`push to main · ${category.label.toLowerCase()} detector starts`, "mid");
      for (const line of s.detectorOutput) {
        await wait(token, 420);
        setEvidence((current) => [...current, line]);
      }
      await wait(token, 400);
      say(`detector failed on ${s.file}`, "red");
      stage("detect", "done");

      stage("jury", "active");
      say("Skeptic and Corroborator argue in parallel", "violet");
      await wait(token, 700);
      const skeptic = falsePositive ? 74 : s.skeptic.confidence;
      const corroborator = falsePositive ? 63 : s.corroborator.confidence;
      setJury({ skeptic, corroborator });
      await wait(token, 1100);
      say(`Skeptic · ${skeptic}% sure it is NOT a bug`, "violet");
      say(`Corroborator · ${corroborator}% sure it IS a bug`, "violet");
      stage("jury", "done");

      stage("arbiter", "active");
      say("mechanical recheck · detector re-run on a clean checkout: still fails", "mid");
      await wait(token, 800);

      if (ambiguous && !falsePositive) {
        say("Arbiter · this turns on product intent, not evidence. Asking instead of guessing.", "amber");
        stage("arbiter", "fail");
        setPhase("question");
        return;
      }

      const gap = Math.abs(100 - skeptic - corroborator);
      if (gap > DISAGREEMENT_THRESHOLD) {
        setMeta(true);
        say(`jury gap ${gap} > ${DISAGREEMENT_THRESHOLD} · Meta-Auditor called on the strongest model`, "amber");
        await wait(token, 1100);
      }
      const finalScore = falsePositive ? 41 : s.score;
      setScore(finalScore);
      await wait(token, 700);
      say(`Arbiter · assurance ${finalScore}/100 (threshold ${category.assurance})`, "amber");

      if (finalScore < category.assurance) {
        stage("arbiter", "done");
        for (const key of ["patch", "verify", "decide", "ship"] as StageKey[]) stage(key, "skip");
        say("held · no GitHub issue, nobody paged, kept for calibration", "mid");
        setOutcome({
          tone: "mid",
          title: "Held, not raised",
          body: `The jury disagreed, the Meta-Auditor sided with the Skeptic, and ${finalScore} is under this category's bar of ${category.assurance}. A finding that can't clear it never reaches your repository.`,
        });
        setPhase("done");
        return;
      }
      stage("arbiter", "done");
      say(`issue raised on GitHub · #142 ${s.title}`, "amber");
      await wait(token, 500);
      await patchAndVerify(token, s, { fail: failFirst });
    } catch (error) {
      if (!(error instanceof Cancelled)) throw error;
    }
  }

  async function answerQuestion(isBug: boolean) {
    const token = runToken.current;
    const s = scenario;
    setPhase("running");
    try {
      say(isBug ? "you: it's a bug" : "you: that's intended behaviour", "green");
      await wait(token, 500);
      if (!isBug) {
        for (const key of ["patch", "verify", "decide", "ship"] as StageKey[]) stage(key, "skip");
        stage("arbiter", "done");
        say("closed as intended · recorded so the same finding is not raised again", "mid");
        setOutcome({
          tone: "mid",
          title: "Closed as intended",
          body: "Your answer is now part of the record. The council asked because the evidence could not settle it. A confident number here would have been invented.",
        });
        setPhase("done");
        return;
      }
      setScore(s.score);
      stage("arbiter", "done");
      say(`Arbiter · re-scored with your answer: ${s.score}/100`, "amber");
      say(`issue raised on GitHub · #142 ${s.title}`, "amber");
      await wait(token, 500);
      await patchAndVerify(token, s, { fail: failFirst });
    } catch (error) {
      if (!(error instanceof Cancelled)) throw error;
    }
  }

  async function decide(verb: "approve" | "changes" | "reject") {
    const token = runToken.current;
    const s = scenario;
    if (verb === "changes") {
      setPhase("notes");
      return;
    }
    setPhase("running");
    try {
      if (verb === "reject") {
        stage("decide", "done");
        stage("ship", "skip");
        say("you rejected the fix", "red");
        await wait(token, 400);
        say("repository untouched · no branch, no pull request", "mid");
        setOutcome({
          tone: "red",
          title: "Rejected. Nothing happened.",
          body: "The proposal only ever existed in WhipGuard: the diff was stored for review, never pushed. Rejecting it leaves your repository exactly as it was.",
        });
        setPhase("done");
        return;
      }
      stage("decide", "done");
      stage("ship", "active");
      say("you approved", "green");
      await wait(token, 600);
      const slug = s.title.toLowerCase().replace(/[^a-z0-9]+/g, "-").slice(0, 24).replace(/-$/, "");
      say(`branch pushed · whipguard/fix-142-${slug}`, "mid");
      await wait(token, 700);
      say("pull request #143 opened, linked to issue #142", "mid");
      await wait(token, 800);
      say("preview deployed · fix-142.preview.pages.dev", "mid");
      await wait(token, 900);
      say(`${category.detector} re-run against the LIVE preview · ${s.previewCheck}`, "green");
      await wait(token, 900);
      say("outcome check · GitHub, the deploy, Slack and the dashboard read back independently: they agree", "green");
      stage("ship", "done");
      setOutcome({
        tone: "green",
        title: "Pull request open. Your merge.",
        body: "Verified twice: in a sandbox, then against a live preview. Now it waits for a person to merge it on GitHub. WhipGuard has no merge capability at all. Once you merge, the dashboard syncs and the branch is cleaned up.",
      });
      setPhase("done");
    } catch (error) {
      if (!(error instanceof Cancelled)) throw error;
    }
  }

  async function sendNote(text: string) {
    const token = runToken.current;
    const trimmed = text.trim();
    if (!trimmed) return;
    setPhase("running");
    stage("decide", "idle");
    say(`you asked for changes: "${trimmed}"`, "amber");
    try {
      await wait(token, 500);
      await patchAndVerify(token, scenario, { fail: false, reviewerNote: trimmed });
    } catch (error) {
      if (!(error instanceof Cancelled)) throw error;
    }
  }

  const running = phase === "running";
  const gap = jury ? Math.abs(100 - jury.skeptic - jury.corroborator) : 0;

  return (
    <div className="lp-sim">
      {/* -------- controls -------- */}
      <div className="lp-sim-side">
        <p className="lp-k">Pick a bug</p>
        <div className="lp-scenarios" role="radiogroup" aria-label="Bug scenario">
          {SCENARIOS.map((s) => {
            const c = CATEGORY[s.category];
            const on = s.id === scenarioId;
            return (
              <button
                key={s.id}
                type="button"
                role="radio"
                aria-checked={on}
                className={`lp-scenario ${on ? "on" : ""}`}
                onClick={() => {
                  setScenarioId(s.id);
                  reset();
                }}
              >
                <Icon name={c.icon} size={16} className="shrink-0" />
                <span className="min-w-0">
                  <span className="block text-[13px] text-hi truncate">{s.title}</span>
                  <span className="block text-[11px] text-lo">{c.label} · bar {c.assurance}</span>
                </span>
              </button>
            );
          })}
        </div>

        <p className="lp-k mt-5">What goes wrong</p>
        <Toggle label="First patch fails" hint="watch the verifier reject it" on={failFirst} set={setFailFirst} />
        <Toggle label="It's a false positive" hint="the jury splits, the Meta-Auditor decides" on={falsePositive} set={setFalsePositive} />
        <Toggle label="Intent is ambiguous" hint="the Arbiter asks you instead of guessing" on={ambiguous} set={setAmbiguous} />

        <button type="button" className="btn btn-primary w-full mt-5 py-2.5 shadow-glow" onClick={play} disabled={running}>
          <Icon name={phase === "idle" ? "play" : "loop"} size={15} />
          {phase === "idle" ? "Push a commit" : "Run it again"}
        </button>
      </div>

      {/* -------- the run -------- */}
      <div className="lp-sim-main">
        <div className="lp-stepper" aria-label="Pipeline progress">
          {STAGES.map((st) => (
            <div key={st.key} className={`lp-step s-${stages[st.key]}`}>
              <span className="lp-step-ic">
                <Icon name={stages[st.key] === "fail" ? "halt" : st.icon} size={14} />
              </span>
              <span className="lp-step-l">{st.label}</span>
            </div>
          ))}
        </div>

        <div className="lp-panels">
          <section className="lp-panel">
            <p className="lp-k">Detector · {category.detector}</p>
            <div className="lp-term num" aria-live="polite">
              {evidence.length === 0 ? (
                <span className="text-lo">waiting for a push to {scenario.file}…</span>
              ) : (
                evidence.map((line, i) => (
                  <div key={i} className={`animate-fade-in ${i === 0 ? "text-lo" : i === 1 ? "text-[color:var(--failed)]" : "text-mid"}`}>
                    {line}
                  </div>
                ))
              )}
            </div>
          </section>

          <section className="lp-panel">
            <p className="lp-k">Adversarial jury</p>
            <Bar label="Skeptic: is a bug" sub={jury ? `${100 - jury.skeptic}` : "–"} value={jury ? 100 - jury.skeptic : 0} color="var(--pending)" />
            <Bar label="Corroborator: is a bug" sub={jury ? `${jury.corroborator}` : "–"} value={jury?.corroborator ?? 0} color="var(--amber)" />
            <p className="text-[11px] text-lo mt-2 leading-relaxed">
              {jury ? (
                <>
                  Gap <span className="num text-mid">{gap}</span>{" "}
                  {gap > DISAGREEMENT_THRESHOLD ? (
                    <span style={{ color: "var(--amber)" }}>&gt; {DISAGREEMENT_THRESHOLD}: Meta-Auditor called</span>
                  ) : (
                    <>≤ {DISAGREEMENT_THRESHOLD}: the jury broadly agrees</>
                  )}
                  {meta && <span className="lp-chip ml-1.5">meta-audit</span>}
                </>
              ) : (
                "Two models argue opposite sides. Their disagreement is measured, not felt."
              )}
            </p>
          </section>

          <section className="lp-panel">
            <p className="lp-k">Scores vs this category&apos;s bars</p>
            <Gauge label="Assurance" value={score} bar={category.assurance} />
            <Gauge label="Resolution" value={resolution} bar={category.resolution} />
          </section>

          <section className="lp-panel">
            <p className="lp-k">
              Proposed diff · {scenario.file} {attempt > 0 && <span className="num text-lo">· attempt {attempt}</span>}
            </p>
            <div className="lp-diff num">
              {diffShown ? (
                scenario.diff.map((line, i) => (
                  <div key={i} className={`lp-dl ${line.kind}`}>
                    <span aria-hidden>{line.kind === "add" ? "+" : line.kind === "del" ? "−" : " "}</span>
                    {line.text}
                  </div>
                ))
              ) : (
                <span className="text-lo">No patch yet. A diff is only written once the finding clears its bar.</span>
              )}
            </div>
          </section>
        </div>
      </div>

      {/* -------- the human -------- */}
      <div className="lp-sim-feed">
        <div ref={cardRef} className="lp-card-approve" aria-live="polite">
          <div className="lp-card-head">
            <Icon name="shield" size={14} className="text-accent" />
            <span>WhipGuard</span>
            <span className="ml-auto text-[10px] text-lo">dashboard · Slack · email · push</span>
          </div>
          <div className="lp-card-body">
            {phase === "idle" && (
              <div className="text-center py-6">
                <Icon name="hourglass" size={22} className="mx-auto text-lo" />
                <p className="text-sm text-mid mt-2">Quiet. Push a commit and the decision lands here.</p>
              </div>
            )}
            {phase === "running" && !outcome && (
              <div className="py-5 text-center">
                <span className="dot bg-[color:var(--amber)] animate-pulse-dot inline-block" />
                <p className="text-sm text-mid mt-2">The council is working. You will only be asked when there is something to decide.</p>
              </div>
            )}
            {phase === "question" && (
              <div>
                <p className="lp-k">The Arbiter is asking you</p>
                <p className="text-sm text-hi mt-1 leading-relaxed">{scenario.question}</p>
                <div className="grid grid-cols-2 gap-2 mt-3">
                  <button type="button" className="btn btn-primary py-2 text-sm" onClick={() => answerQuestion(true)}>It&apos;s a bug</button>
                  <button type="button" className="btn btn-ghost py-2 text-sm" onClick={() => answerQuestion(false)}>That&apos;s intended</button>
                </div>
              </div>
            )}
            {phase === "decision" && (
              <div>
                <p className="lp-k">Fix ready for review</p>
                <p className="text-sm text-hi mt-1">{scenario.title}</p>
                <p className="text-[12px] text-mid mt-1">
                  Assurance <span className="num">{score}</span> · resolution <span className="num">{resolution}</span> · verified in a sandbox
                </p>
                <div className="grid gap-2 mt-3">
                  <button type="button" className="btn btn-primary py-2 text-sm" onClick={() => decide("approve")}>
                    <Icon name="approve" size={15} /> Approve
                  </button>
                  <div className="grid grid-cols-2 gap-2">
                    <button type="button" className="btn btn-ghost py-2 text-sm" onClick={() => decide("changes")}>
                      <Icon name="chat" size={15} /> Ask for changes
                    </button>
                    <button type="button" className="btn btn-ghost py-2 text-sm lp-danger" onClick={() => decide("reject")}>
                      <Icon name="close" size={15} /> Reject
                    </button>
                  </div>
                </div>
              </div>
            )}
            {phase === "notes" && (
              <div>
                <p className="lp-k">What should change?</p>
                <div className="flex flex-wrap gap-1.5 mt-2">
                  {NOTE_PRESETS.map((preset) => (
                    <button key={preset} type="button" className="lp-chip-btn" onClick={() => sendNote(preset)}>
                      {preset}
                    </button>
                  ))}
                </div>
                <form
                  className="flex gap-2 mt-2"
                  onSubmit={(event) => {
                    event.preventDefault();
                    sendNote(note);
                  }}
                >
                  <input
                    className="lp-input"
                    value={note}
                    maxLength={120}
                    onChange={(event) => setNote(event.target.value)}
                    placeholder="…or write your own"
                    aria-label="Requested change"
                  />
                  <button type="submit" className="btn btn-primary px-3 text-sm">Send</button>
                </form>
              </div>
            )}
            {outcome && (
              <div className="animate-fade-in">
                <p className="text-sm font-semibold" style={{ color: TONE[outcome.tone] === TONE.mid ? "var(--text-hi)" : TONE[outcome.tone] }}>
                  {outcome.title}
                </p>
                <p className="text-[13px] text-mid mt-1.5 leading-relaxed">{outcome.body}</p>
              </div>
            )}
          </div>
        </div>

        <div className="lp-panel mt-3">
          <p className="lp-k">Activity · every step on the record</p>
          <ol ref={logRef} className="lp-log num">
            {log.length === 0 && <li className="text-lo">nothing yet</li>}
            {log.map((line) => (
              <li key={line.id} className="animate-fade-in">
                <span className="text-lo">{line.at}</span>
                <span style={{ color: TONE[line.tone] }}>{line.text}</span>
              </li>
            ))}
          </ol>
        </div>
      </div>
    </div>
  );
}

function Toggle({ label, hint, on, set }: { label: string; hint: string; on: boolean; set: (v: boolean) => void }) {
  return (
    <label className="lp-switch">
      <input type="checkbox" checked={on} onChange={(event) => set(event.target.checked)} />
      <span className="lp-track" aria-hidden>
        <span className="lp-knob" />
      </span>
      <span className="min-w-0">
        <span className="block text-[13px] text-hi">{label}</span>
        <span className="block text-[11px] text-lo">{hint}</span>
      </span>
    </label>
  );
}

function Bar({ label, sub, value, color }: { label: string; sub: string; value: number; color: string }) {
  return (
    <div className="mt-2">
      <div className="flex justify-between text-[11px]">
        <span className="text-mid">{label}</span>
        <span className="num text-hi">{sub}</span>
      </div>
      <div className="lp-bar">
        <span style={{ width: `${value}%`, background: color }} />
      </div>
    </div>
  );
}

function Gauge({ label, value, bar }: { label: string; value: number | null; bar: number }) {
  const passed = value !== null && value >= bar;
  return (
    <div className="mt-2.5">
      <div className="flex justify-between text-[11px]">
        <span className="text-mid">{label}</span>
        <span className="num" style={{ color: value === null ? "var(--text-lo)" : passed ? "var(--verified)" : "var(--failed)" }}>
          {value === null ? "–" : value} <span className="text-lo">/ bar {bar}</span>
        </span>
      </div>
      <div className="lp-bar lp-gauge">
        <span
          style={{
            width: `${value ?? 0}%`,
            background: value === null ? "transparent" : passed ? "var(--verified)" : "var(--failed)",
          }}
        />
        <i style={{ left: `${bar}%` }} aria-hidden />
      </div>
    </div>
  );
}
