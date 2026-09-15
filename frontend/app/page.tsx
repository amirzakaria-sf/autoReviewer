"use client";

import { useEffect, useState } from "react";

const PIPELINE = [
  {
    step: "1",
    title: "Detect",
    body: "A category-specific detector (Playwright, node --test, a secret scan, a bundle-size budget, a doc-vs-code drift check) runs against the repo on every push.",
  },
  {
    step: "2",
    title: "Adversarial jury",
    body: "A Skeptic and a Corroborator argue opposite sides in parallel, an Arbiter scores it against a written rubric, and a Meta-Auditor only gets called in when the two juries actually disagree.",
  },
  {
    step: "3",
    title: "Human approval",
    body: "Nothing ships without a yes — from Slack, the dashboard, or a GitHub issue comment. If the model is missing product intent rather than evidence, it pauses and asks instead of guessing.",
  },
  {
    step: "4",
    title: "Deploy the branch",
    body: "The fix branch is pushed and deployed to its own live preview URL, then the same check that caught the bug is re-run against that live URL, not just in a sandbox.",
  },
  {
    step: "5",
    title: "Outcome check",
    body: "GitHub, the deploy, Slack, and the dashboard are all read back independently after the fact. Any disagreement between them fails the run closed, even if every step reported success.",
  },
  {
    step: "6",
    title: "Merge & clean up",
    body: "Once a human merges the PR on GitHub, the dashboard syncs automatically and the fix branch is deleted — nothing lingers after the fix lands.",
  },
];

const CATEGORIES = [
  { label: "UI", detail: "Playwright-driven behavioral checks against the running app." },
  { label: "Backend", detail: "node --test / pytest runs catch logic bugs at the source." },
  { label: "Security", detail: "Secret scanning — API keys, credentials, private key blocks." },
  { label: "Performance", detail: "Bundle-size budget enforcement on every change." },
  { label: "Documentation", detail: "Flags README references to functions that no longer exist." },
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
    <div className="min-h-screen">
      <nav className="border-b border-border bg-panel/60 backdrop-blur sticky top-0 z-10">
        <div className="max-w-5xl mx-auto px-4 py-3 flex items-center justify-between">
          <span className="flex items-center gap-2 font-semibold tracking-tight">
            <span className="text-lg">🛡️</span> WhipGuard
          </span>
          <div className="flex items-center gap-4 text-sm">
            <span className={`badge ${health === "up" ? "badge-green" : health === "down" ? "badge-red" : "badge-gray"}`}>
              {health === "checking" ? "checking…" : health === "up" ? "council running" : "unreachable"}
            </span>
            <a href="/login" className="px-3 py-1.5 rounded-md bg-white/10 hover:bg-white/20 font-medium">
              Sign in
            </a>
          </div>
        </div>
      </nav>

      <header className="max-w-5xl mx-auto px-4 pt-16 pb-14 text-center">
        <h1 className="text-4xl sm:text-5xl font-semibold tracking-tight">
          A bug council that watches your repo
        </h1>
        <p className="mt-4 text-lg text-gray-400 max-w-2xl mx-auto">
          WhipGuard detects real bugs, argues both sides of whether they matter, proposes a
          verified fix, deploys it to a live preview, and waits for a human yes before anything
          touches <code className="text-sm">main</code>.
        </p>
        <div className="mt-8 flex items-center justify-center gap-3">
          <a href="/login" className="px-5 py-2.5 rounded-md bg-white text-black font-medium hover:bg-gray-200">
            Open the dashboard
          </a>
        </div>
      </header>

      <section className="max-w-5xl mx-auto px-4 py-10">
        <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wide mb-6">
          How a bug moves through the pipeline
        </h2>
        <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {PIPELINE.map((p) => (
            <div key={p.step} className="border border-border bg-panel rounded-lg p-4">
              <div className="text-xs text-gray-500 font-mono mb-1">Step {p.step}</div>
              <div className="font-semibold mb-1.5">{p.title}</div>
              <p className="text-sm text-gray-400 leading-relaxed">{p.body}</p>
            </div>
          ))}
        </div>
      </section>

      <section className="max-w-5xl mx-auto px-4 py-10">
        <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wide mb-6">
          Five categories, one detector each
        </h2>
        <div className="grid sm:grid-cols-2 lg:grid-cols-5 gap-3">
          {CATEGORIES.map((c) => (
            <div key={c.label} className="border border-border bg-panel rounded-lg p-4">
              <div className="font-semibold mb-1.5">{c.label}</div>
              <p className="text-xs text-gray-400 leading-relaxed">{c.detail}</p>
            </div>
          ))}
        </div>
      </section>

      <section className="max-w-5xl mx-auto px-4 py-10">
        <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wide mb-6">
          Under the hood
        </h2>
        <div className="grid sm:grid-cols-2 gap-4 text-sm text-gray-400 leading-relaxed">
          <div className="border border-border bg-panel rounded-lg p-4">
            <div className="font-semibold text-white mb-1.5">Retrieval, not guesswork</div>
            A pgvector similarity search over code chunks and past issues, plus a real
            dependency graph, ground every proposed fix in the actual codebase instead of an
            LLM's best guess.
          </div>
          <div className="border border-border bg-panel rounded-lg p-4">
            <div className="font-semibold text-white mb-1.5">Verified, not assumed</div>
            Every fix is re-run against the same check it was raised with — once in a sandbox,
            once against the live deployed preview — before a human is even asked to approve it.
          </div>
        </div>
      </section>

      <footer className="max-w-5xl mx-auto px-4 py-10 text-center text-xs text-gray-600">
        WhipGuard — single-operator bug council, not a hosted product.
      </footer>
    </div>
  );
}
