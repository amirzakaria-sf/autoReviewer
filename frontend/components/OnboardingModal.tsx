"use client";

import { useState } from "react";
import { api } from "@/lib/api";

export function OnboardingModal({ onDone }: { onDone: () => void }) {
  const [dismissing, setDismissing] = useState(false);

  async function dismiss() {
    setDismissing(true);
    try {
      await api.completeOnboarding();
    } finally {
      onDone();
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm">
      <div className="w-full max-w-md card p-7 space-y-5 animate-fade-in">
        <div>
          <div className="text-3xl mb-2">👋</div>
          <h2 className="text-lg font-semibold">Welcome to WhipGuard</h2>
          <p className="text-sm text-gray-400 mt-1.5">
            Connect GitHub to watch a repo, and Slack to get fix-proposed and escalation alerts. You can always do
            this later from your profile.
          </p>
        </div>

        <div className="space-y-2.5">
          <a
            href="/api/github/oauth/start"
            onClick={() => api.completeOnboarding().catch(() => {})}
            className="btn btn-primary w-full py-2.5 text-sm"
          >
            Connect GitHub
          </a>
          <a
            href="/connect"
            onClick={() => api.completeOnboarding().catch(() => {})}
            className="btn btn-ghost w-full py-2.5 text-sm"
          >
            Pick a repo first
          </a>
        </div>

        <button onClick={dismiss} disabled={dismissing} className="w-full text-center text-xs text-gray-500 hover:text-gray-300 transition">
          Skip for now
        </button>
      </div>
    </div>
  );
}
