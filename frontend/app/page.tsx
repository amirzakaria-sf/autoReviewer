"use client";

import { useEffect, useState } from "react";

const PIPELINE = [
  {
    icon: "🔎",
    title: "Detect",
    body: "A category-specific detector (Playwright, node --test, a secret scan, a bundle-size budget, an axe-core check, a doc-vs-code drift check) runs against the repo on every push.",
  },
  {
    icon: "⚖️",
    title: "Adversarial jury",
    body: "A Skeptic and a Corroborator argue opposite sides in parallel, an Arbiter scores it against a written rubric, and a Meta-Auditor only gets called in when the two juries actually disagree.",
  },
  {
    icon: "🙋",
    title: "Human approval",
    body: "Nothing ships without a yes — from Slack, email, the dashboard, or a GitHub comment. Missing product intent, not evidence? It pauses and asks instead of guessing.",
  },
  {
    icon: "🚀",
    title: "Deploy the branch",
    body: "The fix branch is pushed and deployed to its own live preview URL, then the same check that caught the bug is re-run against that live URL, not just in a sandbox.",
  },
  {
    icon: "🔁",
    title: "Outcome check",
    body: "GitHub, the deploy, Slack, and the dashboard are all read back independently afterward. Any disagreement between them fails the run closed, even if every step reported success.",
  },
  {
    icon: "🧹",
    title: "Merge & clean up",
    body: "Once a human merges the PR on GitHub, the dashboard syncs automatically and the fix branch is deleted — nothing lingers after the fix lands.",
  },
];

const CATEGORIES = [
  { label: "UI", icon: "🖥️", ring: "ring-blue-500/30", dot: "bg-blue-400", detail: "Playwright-driven behavioral checks against the running app." },
  { label: "Backend", icon: "⚙️", ring: "ring-violet-500/30", dot: "bg-violet-400", detail: "node --test / pytest runs catch logic bugs at the source." },
  { label: "Security", icon: "🔒", ring: "ring-red-500/30", dot: "bg-red-400", detail: "Secret scanning — API keys, credentials, private key blocks." },
  { label: "Performance", icon: "⚡", ring: "ring-yellow-500/30", dot: "bg-yellow-400", detail: "Bundle-size budget enforcement on every change." },
  { label: "Accessibility", icon: "♿", ring: "ring-emerald-500/30", dot: "bg-emerald-400", detail: "axe-core WCAG checks — contrast, labels, ARIA." },
  { label: "Documentation", icon: "📝", ring: "ring-cyan-500/30", dot: "bg-cyan-400", detail: "Flags README references to functions that no longer exist." },
];

type Health = "checking" | "up" | "down";

export default function LandingPage() {
  const [health, setHealth] = useState<Health>("checking");

  useEffect(() => {
    // /api/auth/session is reachable pre-login (see main.py's _PUBLIC_PATHS)
    // and, unlike /healthz, is actually routed here -- nginx only proxies
    // /api/ and /ws/ to the backend; a bare /healthz falls through to the
    // frontend's own catch-all route and 404s even when the backend is fine.
    fetch("/api/auth/session", { cache: "no-store", credentials: "include" })
      .then((r) => setHealth(r.ok ? "up" : "down"))
      .catch(() => setHealth("down"));
  }, []);

  return (
    <div className="min-h-screen relative overflow-hidden">
      {/* Ambient glow orbs -- fixed, behind everything, decorative only */}
      <div className="pointer-events-none fixed inset-0 -z-10 overflow-hidden">
        <div className="absolute -top-32 left-1/4 w-[36rem] h-[36rem] rounded-full bg-accent/10 blur-3xl" />
        <div className="absolute top-1/3 -right-40 w-[28rem] h-[28rem] rounded-full bg-violet-500/10 blur-3xl" />
        <div className="absolute bottom-0 left-1/3 w-[24rem] h-[24rem] rounded-full bg-emerald-500/5 blur-3xl" />
      </div>

      <nav className="border-b border-border bg-panel/60 backdrop-blur-md sticky top-0 z-10">
        <div className="max-w-5xl mx-auto px-4 py-3 flex items-center justify-between">
          <span className="flex items-center gap-2 font-semibold tracking-tight">
            <span className="text-lg">🛡️</span> WhipGuard
          </span>
          <div className="flex items-center gap-3 text-sm">
            <span className={`badge hidden sm:inline-flex ${health === "up" ? "badge-green" : health === "down" ? "badge-red" : "badge-gray"}`}>
              <span className={`dot ${health === "up" ? "bg-green-400 animate-pulse-dot" : health === "down" ? "bg-red-400" : "bg-gray-400"}`} />
              {health === "checking" ? "checking…" : health === "up" ? "council running" : "unreachable"}
            </span>
            <a href="/signup" className="text-mid hover:text-hi transition hidden sm:inline">Request access</a>
            <a href="/login" className="btn btn-ghost px-4 py-1.5 font-medium">Sign in</a>
          </div>
        </div>
      </nav>

      <header className="max-w-4xl mx-auto px-4 pt-20 pb-16 text-center relative">
        <div className="inline-flex items-center gap-2 badge badge-accent mb-6">
          <span className="dot bg-accent-soft" /> Multi-agent bug council, not a linter
        </div>
        <h1 className="text-4xl sm:text-6xl font-semibold tracking-tight leading-[1.08]">
          A bug council that
          <br />
          <span className="bg-gradient-to-r from-accent-soft via-accent to-violet-400 bg-clip-text text-transparent">
            watches your repo
          </span>
        </h1>
        <p className="mt-6 text-lg text-mid max-w-2xl mx-auto leading-relaxed">
          WhipGuard detects real bugs, argues both sides of whether they matter, proposes a
          verified fix, deploys it to a live preview, and waits for a human yes before anything
          touches <code className="text-sm bg-white/5 px-1.5 py-0.5 rounded border border-border">main</code>.
        </p>
        <div className="mt-9 flex items-center justify-center gap-3 flex-wrap">
          <a href="/login" className="btn btn-primary px-6 py-3 text-sm shadow-glow">
            Open the dashboard
          </a>
          <a href="/signup" className="btn btn-ghost px-6 py-3 text-sm">
            Request access
          </a>
        </div>
      </header>

      <section className="max-w-5xl mx-auto px-4 py-14">
        <div className="text-center mb-10">
          <div className="section-label">The pipeline</div>
          <h2 className="text-2xl font-semibold mt-2">How a bug moves end to end</h2>
        </div>
        <div className="relative">
          {/* Connecting line, desktop only */}
          <div className="hidden lg:block absolute top-[38px] left-[8%] right-[8%] h-px bg-gradient-to-r from-transparent via-border2 to-transparent" />
          <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-5">
            {PIPELINE.map((p, i) => (
              <div key={p.title} className="card card-hover p-5 relative">
                <div className="flex items-center gap-3 mb-2.5">
                  <div className="w-9 h-9 rounded-lg bg-white/5 border border-border flex items-center justify-center text-base shrink-0">
                    {p.icon}
                  </div>
                  <div className="text-xs text-lo font-mono">{String(i + 1).padStart(2, "0")}</div>
                  <div className="font-semibold">{p.title}</div>
                </div>
                <p className="text-sm text-mid leading-relaxed">{p.body}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="max-w-5xl mx-auto px-4 py-14">
        <div className="text-center mb-10">
          <div className="section-label">Coverage</div>
          <h2 className="text-2xl font-semibold mt-2">Six categories, one detector each</h2>
          <p className="text-sm text-lo mt-2 max-w-xl mx-auto">
            A new category is a config row plus a detector module — never new pipeline code.
          </p>
        </div>
        <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {CATEGORIES.map((c) => (
            <div key={c.label} className={`card card-hover p-4 ring-1 ${c.ring}`}>
              <div className="flex items-center gap-2.5 mb-1.5">
                <span className="text-base">{c.icon}</span>
                <span className="font-semibold text-sm">{c.label}</span>
                <span className={`dot ${c.dot} ml-auto`} />
              </div>
              <p className="text-xs text-mid leading-relaxed">{c.detail}</p>
            </div>
          ))}
        </div>
      </section>

      <section className="max-w-5xl mx-auto px-4 py-14">
        <div className="text-center mb-10">
          <div className="section-label">Under the hood</div>
          <h2 className="text-2xl font-semibold mt-2">Grounded, not guessed</h2>
        </div>
        <div className="grid sm:grid-cols-2 gap-5">
          <div className="card p-5">
            <div className="text-2xl mb-3">🧭</div>
            <div className="font-semibold text-white mb-1.5">Retrieval, not guesswork</div>
            <p className="text-sm text-mid leading-relaxed">
              A pgvector similarity search over code chunks and past issues, plus a real
              dependency graph, ground every proposed fix in the actual codebase instead of an
              LLM's best guess.
            </p>
          </div>
          <div className="card p-5">
            <div className="text-2xl mb-3">✅</div>
            <div className="font-semibold text-white mb-1.5">Verified, not assumed</div>
            <p className="text-sm text-mid leading-relaxed">
              Every fix is re-run against the same check it was raised with — once in a sandbox,
              once against the live deployed preview — before a human is even asked to approve it.
            </p>
          </div>
          <div className="card p-5">
            <div className="text-2xl mb-3">🛑</div>
            <div className="font-semibold text-white mb-1.5">A kill switch that means it</div>
            <p className="text-sm text-mid leading-relaxed">
              Pause detection or fix proposals per repo in one click. A runaway detector has to
              be stoppable, not just theoretically configurable.
            </p>
          </div>
          <div className="card p-5">
            <div className="text-2xl mb-3">📊</div>
            <div className="font-semibold text-white mb-1.5">Every score, traceable</div>
            <p className="text-sm text-mid leading-relaxed">
              Real token counts, latency, and cost tracked per model call — never a number on
              the dashboard nobody can trace back to a reason.
            </p>
          </div>
        </div>
      </section>

      <section className="max-w-3xl mx-auto px-4 py-16 text-center">
        <div className="card p-10 bg-gradient-to-b from-accent-dim/40 to-transparent">
          <h2 className="text-2xl font-semibold mb-3">Ready to watch your repo?</h2>
          <p className="text-sm text-mid mb-7 max-w-md mx-auto">
            Requests are reviewed by an admin — nothing is created until it's approved.
          </p>
          <div className="flex items-center justify-center gap-3 flex-wrap">
            <a href="/signup" className="btn btn-primary px-6 py-3 text-sm shadow-glow">Request access</a>
            <a href="/login" className="btn btn-ghost px-6 py-3 text-sm">Sign in</a>
          </div>
        </div>
      </section>

      <footer className="max-w-5xl mx-auto px-4 py-10 text-center text-xs text-lo border-t border-border">
        WhipGuard — a bug council for repos that means it, not a hosted product.
      </footer>
    </div>
  );
}
