"use client";

import { useEffect, useState } from "react";
import { Icon, type IconName } from "@/components/Icon";
import { BeforeAfter } from "@/components/landing/BeforeAfter";
import { InteractiveBackdrop } from "@/components/landing/InteractiveBackdrop";
import { JuryLab } from "@/components/landing/JuryLab";
import { PipelineSteps } from "@/components/landing/PipelineSteps";
import { RunReplay } from "@/components/landing/RunReplay";
import { CATEGORIES } from "@/components/landing/sim-data";
import { Simulator } from "@/components/landing/Simulator";
import { TrickIt } from "@/components/landing/TrickIt";

const SECTIONS = [
  { id: "how", label: "How it works" },
  { id: "simulator", label: "Simulator" },
  { id: "jury", label: "The jury" },
  { id: "coverage", label: "Coverage" },
  { id: "guardrails", label: "Guardrails" },
  { id: "faq", label: "FAQ" },
];

const MARQUEE = [
  "Wrong cart totals", "Rows that won't delete", "Committed credentials", "Blown bundle budgets", "Contrast failures",
  "Stale READMEs", "An adversarial jury", "Verified twice", "Live preview re-checks", "Three verbs, never a merge",
];

const RAILS: { icon: IconName; title: string; body: string }[] = [
  { icon: "merge", title: "It never merges", body: "There is no merge capability in the codebase, and there must not be one. A merged pull request is always a person's act." },
  { icon: "lock", title: "Nothing leaves before a yes", body: "A proposal is a stored diff in the dashboard. No branch, no pull request and no comment reaches GitHub until a person approves." },
  { icon: "chat", title: "Three verbs, not two", body: "Approve, ask for changes, or reject. Asking for changes runs another attempt with your note, and rejecting leaves the repository untouched." },
  { icon: "key", title: "Secrets stay secret", body: "The scanner names the file, the line and the kind of credential. The value is never copied into a finding, a log or a message." },
  { icon: "eye", title: "Repository text is data", body: "Code, comments and test output are evidence for the models, never instructions. The same goes for anything a model wrote." },
  { icon: "server", title: "Untrusted code runs elsewhere", body: "The process that renders model output has no Docker socket. Cloning, patching, sandboxing and deploys run in a separate worker with no inbound port." },
  { icon: "halt", title: "A kill switch that means it", body: "Pause detection or fix proposals per repository, or everywhere at once. Stopping it is one click, not a config change." },
  { icon: "team", title: "Tenancy is one boundary", body: "Every read and every action is scoped through one function. Another organisation's repository is a 404, never a 403." },
];

const CHANNELS: { icon: IconName; title: string; body: string }[] = [
  { icon: "monitor", title: "Dashboard", body: "The diff, the evidence, every juror's argument and the weighted factors behind each score." },
  { icon: "chat", title: "Slack", body: "A card in the repository's channel, with the decision one tap away." },
  { icon: "mail", title: "Email", body: "A signed, expiring link per decision. Whichever surface acts first, a decision is only made once." },
  { icon: "phone", title: "Push", body: "Install it to your home screen and a fix that needs you buzzes your phone." },
];

const FAQ: { q: string; a: string }[] = [
  { q: "Will it change my repository without asking?", a: "No. Detection and fixing happen in WhipGuard's own sandbox. The proposal is stored as a diff for review, and nothing reaches GitHub (no branch, no pull request, no comment) until a person approves it. Even then it stops at an open pull request: WhipGuard cannot merge." },
  { q: "What if the bug isn't real?", a: "That is what the jury is for. A Skeptic argues against every finding, the detector is re-run on a clean checkout, and an Arbiter scores the result against the category's bar. A close split calls a Meta-Auditor. A finding that cannot clear its bar is held, and you are never paged for it." },
  { q: "What if the fix is wrong?", a: "The same check that caught the bug has to pass on the patched branch before you are asked, and a second skeptic looks for regressions. After approval the check runs once more against the live preview. If GitHub, the deploy, Slack and the dashboard disagree about what happened, the run fails closed." },
  { q: "What if it can't tell whether something is a bug?", a: "When the answer depends on product intent nobody documented, like a hard-coded value that might be deliberate, the Arbiter asks you a specific question instead of inventing a confident number. Your answer is recorded, so the same finding is not raised again." },
  { q: "Can someone manipulate it through a pull request?", a: "Repository content goes to the models labelled as untrusted data, and the tools a patch worker holds can only read and edit files inside its category's scope. Approving, pushing and merging are not tools it has. Approval is a person, and merging is not a capability at all." },
  { q: "Which models does it use, and where?", a: "Open-weight models served by Amazon Bedrock in the EU (Stockholm), signed with the server's own AWS role, so there are no model API keys to leak. Different roles use different models: a fast one for the jury, a stronger one for the Arbiter and the patch loop." },
  { q: "What does it cover today?", a: "Six categories, each one a detector plus a written rubric: UI behaviour (Playwright), backend logic (node --test or pytest), committed secrets, bundle-size budgets, accessibility (axe-core) and documentation drift. A new category is a registry row and a detector, never new pipeline code." },
  { q: "How do I stop it?", a: "The kill switch pauses detection or fix proposals for one repository or for everything, immediately." },
];

type Health = "checking" | "up" | "down";

export default function LandingPage() {
  const [health, setHealth] = useState<Health>("checking");
  const [progress, setProgress] = useState(0);
  const [current, setCurrent] = useState("");
  const [openCard, setOpenCard] = useState<string | null>(null);

  useEffect(() => {
    // /api/auth/session is reachable signed out and, unlike /healthz, is
    // routed to the backend by nginx.
    fetch("/api/auth/session", { cache: "no-store", credentials: "include" })
      .then((r) => setHealth(r.ok ? "up" : "down"))
      .catch(() => setHealth("down"));
  }, []);

  // Scroll progress, read on the next frame rather than on every event.
  useEffect(() => {
    let frame = 0;
    const onScroll = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        const max = document.documentElement.scrollHeight - window.innerHeight;
        setProgress(max > 0 ? Math.min(1, window.scrollY / max) : 0);
      });
    };
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("scroll", onScroll);
    };
  }, []);

  // Section highlighting and reveal-on-scroll. Reveal is progressive: the
  // content is visible without JavaScript, and only gains its entrance once
  // the observer is attached (the `lp-js` class).
  useEffect(() => {
    if (typeof IntersectionObserver === "undefined") return;
    document.documentElement.classList.add("lp-js");
    const reveal = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            entry.target.classList.add("in");
            reveal.unobserve(entry.target);
          }
        }
      },
      { threshold: 0.12 },
    );
    document.querySelectorAll(".lp-reveal").forEach((node) => reveal.observe(node));
    const spy = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) if (entry.isIntersecting) setCurrent(entry.target.id);
      },
      { rootMargin: "-45% 0px -50% 0px" },
    );
    SECTIONS.forEach((s) => {
      const node = document.getElementById(s.id);
      if (node) spy.observe(node);
    });
    return () => {
      reveal.disconnect();
      spy.disconnect();
      document.documentElement.classList.remove("lp-js");
    };
  }, []);

  return (
    <div className="min-h-screen relative lp">
      <div className="lp-progress" aria-hidden>
        <span style={{ transform: `scaleX(${progress})` }} />
      </div>
      <div className="pointer-events-none fixed inset-0 -z-20 overflow-hidden">
        <div className="absolute -top-40 left-1/4 w-[40rem] h-[40rem] rounded-full bg-[rgba(255,178,36,0.07)] blur-3xl" />
        <div className="absolute top-1/2 -right-40 w-[30rem] h-[30rem] rounded-full bg-[rgba(167,139,250,0.055)] blur-3xl" />
      </div>
      <InteractiveBackdrop />

      <nav
        className="sticky top-0 z-30 border-b"
        style={{
          background: "color-mix(in srgb, var(--ink-900) 72%, transparent)",
          borderColor: "color-mix(in srgb, var(--ink-700) 70%, transparent)",
          backdropFilter: "blur(14px) saturate(140%)",
          WebkitBackdropFilter: "blur(14px) saturate(140%)",
          paddingTop: "env(safe-area-inset-top)",
        }}
      >
        <div className="max-w-6xl mx-auto px-4 sm:px-6 h-14 flex items-center justify-between gap-4">
          <a href="#top" className="flex items-center gap-2 font-semibold tracking-tight shrink-0">
            <Icon name="shield" size={18} className="text-accent" /> WhipGuard
          </a>
          <div className="hidden lg:flex items-center gap-1 text-[13px]">
            {SECTIONS.map((s) => (
              <a key={s.id} href={`#${s.id}`} className={`lp-navlink ${current === s.id ? "on" : ""}`}>
                {s.label}
              </a>
            ))}
          </div>
          <div className="flex items-center gap-3 text-sm shrink-0">
            <span className={`badge hidden sm:inline-flex ${health === "up" ? "badge-green" : health === "down" ? "badge-red" : "badge-gray"}`}>
              <span className={`dot ${health === "up" ? "bg-[color:var(--verified)] animate-pulse-dot" : health === "down" ? "bg-[color:var(--failed)]" : "bg-[color:var(--ink-600)]"}`} />
              {health === "checking" ? "checking…" : health === "up" ? "council running" : "unreachable"}
            </span>
            <a href="/login" className="btn btn-ghost px-4 py-1.5 font-medium">Sign in</a>
          </div>
        </div>
      </nav>

      <main id="top">
        {/* ============ HERO ============ */}
        <header className="max-w-6xl mx-auto px-4 sm:px-6 pt-14 sm:pt-20 pb-10 lp-hero">
          <div className="min-w-0">
            <div className="inline-flex items-center gap-2 badge badge-accent mb-6 lp-reveal">
              <span className="dot bg-accent-soft animate-pulse-dot" /> An AI bug council, not a linter
            </div>
            <h1 className="text-4xl sm:text-6xl font-semibold tracking-tight leading-[1.06] lp-reveal">
              Bugs found, argued,
              <br />
              fixed and <span className="lp-grad">proven.</span>
            </h1>
            <p className="mt-6 text-lg text-mid max-w-xl leading-relaxed lp-reveal">
              WhipGuard watches your repository, puts every finding in front of a jury that tries to
              dismiss it, writes a fix that has to pass the same check that caught the bug, and then
              waits for <strong className="text-hi font-medium">your yes</strong> before anything reaches GitHub.
            </p>
            <div className="mt-8 flex items-center gap-3 flex-wrap lp-reveal">
              <a href="#simulator" className="btn btn-primary px-6 py-3 text-sm shadow-glow">
                <Icon name="play" size={15} /> Run the simulator
              </a>
              <a href="/login" className="btn btn-ghost px-6 py-3 text-sm">Open the dashboard</a>
            </div>
            <ul className="lp-stats lp-reveal" aria-label="Highlights">
              <li><b>6</b><span>categories, each with its own detector and bar</span></li>
              <li><b>2×</b><span>verified: in a sandbox, then on a live preview</span></li>
              <li><b>0</b><span>merges. It has no merge button at all</span></li>
            </ul>
          </div>
          <div className="min-w-0 lp-reveal">
            <RunReplay />
            <p className="mt-3 text-center text-xs text-lo leading-relaxed">
              A replay of a real run, rejected attempt included. The gates are the product.
            </p>
          </div>
        </header>

        <div className="lp-marquee" aria-hidden>
          <div className="lp-marquee-track">
            {[...MARQUEE, ...MARQUEE].map((item, i) => (
              <span key={i}>{item}</span>
            ))}
          </div>
        </div>

        {/* ============ BEFORE / AFTER ============ */}
        <section className="lp-section">
          <div className="max-w-5xl mx-auto px-4 sm:px-6">
            <Head eyebrow="The Friday-evening bug" title={<>Same bug. <span className="lp-grad">Two very different weekends.</span></>}
              sub="Drag the handle. On the left, how most regressions still get found and fixed. On the right, the same bug with WhipGuard watching the repository." />
            <div className="lp-reveal"><BeforeAfter /></div>
            <p className="text-center text-xs text-lo mt-3">Illustrative. Your Fridays may vary, and that is the point.</p>
          </div>
        </section>

        {/* ============ HOW ============ */}
        <section className="lp-section" id="how">
          <div className="max-w-5xl mx-auto px-4 sm:px-6">
            <Head eyebrow="How it works" title={<>From a push to <span className="lp-grad">a fix you can trust</span> in six moves.</>}
              sub="Every step leaves a record, and every step can say no. Click any step to read it." />
            <div className="lp-reveal"><PipelineSteps /></div>
          </div>
        </section>

        {/* ============ SIMULATOR ============ */}
        <section className="lp-section" id="simulator">
          <div className="max-w-6xl mx-auto px-4 sm:px-6">
            <Head eyebrow="Live simulator" title={<>Pick a bug. <span className="lp-grad">You&apos;re the approver.</span></>}
              sub="Each scenario replays a run end to end with time compressed. Flip what goes wrong, then make the call yourself. The thresholds and routing rules are the product's real ones." />
            <div className="lp-reveal"><Simulator /></div>
            <p className="text-center text-xs text-lo mt-3">Simulated in your browser. No repository, model or account is touched.</p>
          </div>
        </section>

        {/* ============ JURY ============ */}
        <section className="lp-section" id="jury">
          <div className="max-w-5xl mx-auto px-4 sm:px-6">
            <Head eyebrow="Judgement, measured" title={<>Raising a bug is easy. <span className="lp-grad">Knowing when not to is the product.</span></>}
              sub="Two models argue opposite sides, and their disagreement is a number with a rule attached, not a feeling. Move the sliders and watch the same rule the council runs decide what happens." />
            <div className="lp-reveal"><JuryLab /></div>
          </div>
        </section>

        {/* ============ COVERAGE ============ */}
        <section className="lp-section" id="coverage">
          <div className="max-w-5xl mx-auto px-4 sm:px-6">
            <Head eyebrow="Coverage" title={<>Six categories. <span className="lp-grad">One detector each.</span></>}
              sub="A category is a registry row plus a detector module, never new pipeline code. Open a card to see how it decides, and what it is never allowed to touch." />
            <div className="lp-cover">
              {CATEGORIES.map((c) => {
                const open = openCard === c.key;
                return (
                  <button
                    key={c.key}
                    type="button"
                    className={`lp-ccard card lp-reveal ${open ? "open" : ""}`}
                    aria-expanded={open}
                    onClick={() => setOpenCard(open ? null : c.key)}
                  >
                    <span className="flex items-center gap-2.5">
                      <span className="lp-ccard-ic"><Icon name={c.icon} size={17} /></span>
                      <span className="font-semibold">{c.label}</span>
                      <span className="ml-auto num text-[11px] text-lo">{c.detector}</span>
                    </span>
                    <span className="block text-[13px] text-mid mt-2.5 leading-relaxed">{c.catches}</span>
                    <span className="lp-ccard-more">
                      <span><b>Raised at</b> assurance ≥ <span className="num">{c.assurance}</span></span>
                      <span><b>Fix accepted at</b> resolution ≥ <span className="num">{c.resolution}</span></span>
                      <span><b>Verified by</b> {c.verifiedBy}</span>
                      <span><b>Write scope</b> {c.scopeRule}</span>
                    </span>
                    <span className="lp-ccard-toggle text-[11px] text-lo">{open ? "Less" : "How it decides"}</span>
                  </button>
                );
              })}
            </div>
          </div>
        </section>

        {/* ============ GUARDRAILS ============ */}
        <section className="lp-section" id="guardrails">
          <div className="max-w-6xl mx-auto px-4 sm:px-6">
            <Head eyebrow="Guardrails" title={<>Hard limits, <span className="lp-grad">enforced in code.</span></>}
              sub="Not an AI with good intentions. Rules that hold even when a model is wrong, or is being lied to." />
            <div className="lp-rails">
              {RAILS.map((r) => (
                <div key={r.title} className="card card-hover p-5 lp-reveal">
                  <span className="lp-rail-ic"><Icon name={r.icon} size={18} /></span>
                  <h3 className="font-semibold mt-3">{r.title}</h3>
                  <p className="text-sm text-mid mt-1.5 leading-relaxed">{r.body}</p>
                </div>
              ))}
            </div>
            <div className="mt-10 lp-reveal">
              <p className="section-label text-center">Try to trick it</p>
              <div className="mt-3"><TrickIt /></div>
            </div>
          </div>
        </section>

        {/* ============ CHANNELS ============ */}
        <section className="lp-section">
          <div className="max-w-5xl mx-auto px-4 sm:px-6 lp-split">
            <div className="lp-reveal">
              <p className="section-label">Where you decide</p>
              <h2 className="text-2xl sm:text-3xl font-semibold mt-2 leading-tight">
                It does the digging.<br /><span className="lp-grad">You keep the decision.</span>
              </h2>
              <p className="text-sm text-mid mt-3 leading-relaxed max-w-md">
                The decision reaches you wherever you already are, with the evidence attached, and every
                surface ends in the same approval step. One decision, one record, whichever way you made it.
              </p>
              <ul className="lp-ticks mt-4">
                <li>Per-repository Slack channel, chosen through Slack&apos;s own picker</li>
                <li>Invite your team into an organisation, with roles</li>
                <li>Ask Counsel, the repository chat, why any decision was made</li>
              </ul>
            </div>
            <div className="lp-channels">
              {CHANNELS.map((c) => (
                <div key={c.title} className="card p-4 lp-reveal">
                  <span className="lp-rail-ic"><Icon name={c.icon} size={17} /></span>
                  <p className="font-semibold text-sm mt-2.5">{c.title}</p>
                  <p className="text-[12px] text-mid mt-1 leading-relaxed">{c.body}</p>
                </div>
              ))}
            </div>
          </div>
        </section>

        {/* ============ FAQ ============ */}
        <section className="lp-section" id="faq">
          <div className="max-w-3xl mx-auto px-4 sm:px-6">
            <Head eyebrow="FAQ" title={<>The questions <span className="lp-grad">your team will ask.</span></>} />
            <div className="lp-faq lp-reveal">
              {FAQ.map((f) => (
                <details key={f.q}>
                  <summary>{f.q}</summary>
                  <p>{f.a}</p>
                </details>
              ))}
            </div>
          </div>
        </section>

        {/* ============ CTA ============ */}
        <section className="max-w-3xl mx-auto px-4 sm:px-6 py-16 text-center">
          <div className="card p-8 sm:p-10 bg-gradient-to-b from-accent-dim/40 to-transparent lp-reveal">
            <h2 className="text-2xl sm:text-3xl font-semibold">Make your next regression <span className="lp-grad">boring.</span></h2>
            <p className="text-sm text-mid mt-3 mb-7 max-w-md mx-auto">
              Findings that survived a skeptic. Fixes that passed the check twice. A decision that stays yours.
              Access requests are reviewed by an admin.
            </p>
            <div className="flex items-center justify-center gap-3 flex-wrap">
              <a href="/signup" className="btn btn-primary px-6 py-3 text-sm shadow-glow">Request access</a>
              <a href="#simulator" className="btn btn-ghost px-6 py-3 text-sm">Run the simulator again</a>
            </div>
          </div>
        </section>
      </main>

      <footer className="max-w-6xl mx-auto px-4 py-10 text-xs text-lo border-t border-border flex flex-col sm:flex-row gap-2 sm:justify-between">
        <span className="flex items-center gap-1.5"><Icon name="shield" size={13} className="text-accent" /> WhipGuard</span>
        <span>Every run on this page is simulated or replayed.</span>
        <a href="/login" className="hover:text-hi">Sign in</a>
      </footer>
    </div>
  );
}

function Head({ eyebrow, title, sub }: { eyebrow: string; title: React.ReactNode; sub?: string }) {
  return (
    <div className="text-center mb-10 lp-reveal">
      <div className="section-label">{eyebrow}</div>
      <h2 className="text-2xl sm:text-4xl font-semibold mt-2 tracking-tight leading-tight">{title}</h2>
      {sub && <p className="text-sm sm:text-base text-mid mt-3 max-w-2xl mx-auto leading-relaxed">{sub}</p>}
    </div>
  );
}
