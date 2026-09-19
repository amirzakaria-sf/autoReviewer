"use client";

/* Accepting an invitation to join an organization.
 *
 * Ends with a live session rather than a login form. Sending someone back to
 * sign in after they have just set a password is the step where invite flows
 * quietly lose people.
 */

import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { api, type OrgInviteCheck } from "@/lib/api";

function JoinForm() {
  const params = useSearchParams();
  const router = useRouter();
  const token = params.get("token") ?? "";

  const [invite, setInvite] = useState<OrgInviteCheck | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!token) {
      setProblem("This link is missing its invitation token.");
      return;
    }
    api
      .checkOrgInvite(token)
      .then(setInvite)
      // The endpoint distinguishes expired, revoked and already-used, and
      // each reads differently to the person holding the link. Showing its
      // message beats a generic "invalid".
      .catch((err) => setProblem(err instanceof Error ? err.message : "This invitation could not be read."));
  }, [token]);

  async function accept(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      await api.acceptOrgInvite(token, password);
      router.push("/dashboard");
    } catch (err) {
      setProblem(err instanceof Error ? err.message : "Could not accept this invitation.");
      setBusy(false);
    }
  }

  if (problem) {
    return (
      <div className="auth-card">
        <h1 className="text-lg font-semibold mb-2">This invitation cannot be used</h1>
        <p className="text-sm text-mid leading-relaxed mb-6">{problem}</p>
        <a href="/login" className="btn btn-ghost px-5 py-2.5 w-full justify-center">
          Go to sign in
        </a>
      </div>
    );
  }

  if (!invite) {
    return (
      <div className="auth-card">
        <span className="spinner" aria-label="Loading" />
      </div>
    );
  }

  return (
    <div className="auth-card">
      <h1 className="text-lg font-semibold">Join {invite.org_name}</h1>
      <p className="text-sm text-mid mt-2 leading-relaxed">
        Invited as <span className="text-hi">{invite.email}</span> —{" "}
        {invite.role === "org_admin" ? "organization admin" : "member"}, {invite.seniority.toUpperCase()}.
      </p>

      <form onSubmit={accept} className="mt-6 space-y-4">
        {invite.needs_password && (
          <label className="block">
            <span className="section-label">Choose a password</span>
            <input
              id="join-password"
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              autoComplete="new-password"
              minLength={8}
              required
              className="w-full mt-1.5 rounded-lg px-3 py-2 text-sm"
              style={{ background: "var(--ink-900)", border: "1px solid var(--ink-700)", color: "var(--text-hi)" }}
            />
            <span className="text-[11px] text-lo mt-1.5 block">At least 8 characters.</span>
          </label>
        )}

        {!invite.needs_password && (
          <p className="text-xs text-lo leading-relaxed">
            You already have an account with this address, so there is nothing to set up — accepting adds
            you to {invite.org_name}.
          </p>
        )}

        <button
          disabled={busy || (invite.needs_password && password.length < 8)}
          className="btn btn-primary px-5 py-2.5 w-full justify-center"
        >
          {busy ? "Joining…" : `Join ${invite.org_name}`}
        </button>
      </form>
    </div>
  );
}

export default function JoinPage() {
  return (
    <div className="auth-shell">
      {/* useSearchParams needs a Suspense boundary for a statically rendered
          route, otherwise the build fails rather than the page. */}
      <Suspense fallback={<div className="auth-card"><span className="spinner" /></div>}>
        <JoinForm />
      </Suspense>
    </div>
  );
}
