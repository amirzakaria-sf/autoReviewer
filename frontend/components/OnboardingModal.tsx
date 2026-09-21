"use client";

/* The first thing a new account sees.
 *
 * Which is why it has to know HOW the account arrived. Someone who requested
 * access and was approved is setting up a deployment: connecting GitHub is
 * exactly their next step. Someone invited into an existing organization is
 * joining a team that already has repositories connected, and telling them to
 * connect GitHub is both wrong and, for a plain member, not something they
 * have permission to act on.
 */

import { useEffect, useState } from "react";
import { Icon } from "@/components/Icon";
import { api, type OrgOverview } from "@/lib/api";

export function OnboardingModal({ onDone }: { onDone: () => void }) {
  const [dismissing, setDismissing] = useState(false);
  const [org, setOrg] = useState<OrgOverview | null | "none">(null);

  useEffect(() => {
    // "none" and null are different states: null is still loading, "none"
    // means the lookup finished and this person is in no organization.
    api.org().then(setOrg).catch(() => setOrg("none"));
  }, []);

  async function dismiss() {
    setDismissing(true);
    try {
      await api.completeOnboarding();
    } finally {
      onDone();
    }
  }

  // Held back until the org lookup resolves. A modal that renders the wrong
  // message and then rewrites itself is worse than one that appears a beat
  // later.
  if (org === null) return null;

  const joined = org !== "none";
  const canConnect = joined && org.role === "org_admin";

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm">
      <div className="w-full max-w-md card p-7 space-y-5 animate-fade-in">
        <div>
          <div className="mb-2" style={{ color: "var(--amber)" }}><Icon name="spark" size={28} /></div>
          <h2 className="text-lg font-semibold">
            {joined ? `You're in — ${org.name}` : "Welcome to WhipGuard"}
          </h2>
          <p className="text-sm text-mid mt-1.5 leading-relaxed">
            {!joined
              ? "Connect GitHub to watch a repo, and Slack to get fix-proposed and escalation alerts. You can always do this later from your profile."
              : canConnect
                ? "Connect a repository to start watching it, then invite the rest of your team from the Team page."
                : "Bugs found in your team's repositories will be reviewed here. Findings in your areas of expertise are routed to you — the Team page shows which ones those are."}
          </p>
        </div>

        <div className="space-y-2.5">
          {canConnect || !joined ? (
            <>
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
            </>
          ) : (
            <>
              <button onClick={dismiss} disabled={dismissing} className="btn btn-primary w-full py-2.5 text-sm">
                Open the dashboard
              </button>
              <a
                href="/org"
                onClick={() => api.completeOnboarding().catch(() => {})}
                className="btn btn-ghost w-full py-2.5 text-sm"
              >
                See my team and areas
              </a>
            </>
          )}
        </div>

        {(canConnect || !joined) && (
          <button
            onClick={dismiss}
            disabled={dismissing}
            className="w-full text-center text-xs text-lo hover:text-mid transition"
          >
            Skip for now
          </button>
        )}
      </div>
    </div>
  );
}
