"use client";

import { useEffect, useState } from "react";
import { Icon } from "@/components/Icon";
import { api } from "@/lib/api";

export default function AcceptInvitePage() {
  const [token, setToken] = useState<string | null>(null);
  const [invitee, setInvitee] = useState<{ name: string; email: string; otp_required?: boolean; otp_verified?: boolean } | null>(null);
  const [verified, setVerified] = useState(false);
  const [sentTo, setSentTo] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [codeError, setCodeError] = useState<string | null>(null);
  const [codeBusy, setCodeBusy] = useState(false);
  const [checkError, setCheckError] = useState<string | null>(null);
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    const t = new URLSearchParams(window.location.search).get("token");
    setToken(t);
    if (!t) {
      setCheckError("This invite link is missing its token.");
      return;
    }
    api
      .checkInvite(t)
      .then((info) => {
        setInvitee(info);
        setVerified(!info.otp_required || Boolean(info.otp_verified));
      })
      .catch((err) => setCheckError(err instanceof Error ? err.message : "This invite link is invalid."));
  }, []);

  async function sendCode() {
    if (!token) return;
    setCodeBusy(true);
    setCodeError(null);
    try {
      const res = await api.sendInviteCode(token);
      setSentTo(res.sent_to ?? invitee?.email ?? "your email");
    } catch (err) {
      setCodeError(err instanceof Error ? err.message : "Could not send the code.");
    } finally {
      setCodeBusy(false);
    }
  }

  async function verifyCode(e: React.FormEvent) {
    e.preventDefault();
    if (!token) return;
    setCodeBusy(true);
    setCodeError(null);
    try {
      await api.verifyInviteCode(token, code.trim());
      setVerified(true);
    } catch (err) {
      setCodeError(err instanceof Error ? err.message : "That code is not right.");
    } finally {
      setCodeBusy(false);
    }
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!token) return;
    if (password !== confirm) {
      setSubmitError("Passwords don't match.");
      return;
    }
    setLoading(true);
    setSubmitError(null);
    try {
      await api.completeInvite(token, password);
      window.location.href = "/dashboard";
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : "Could not set up your account.");
    } finally {
      setLoading(false);
    }
  }

  if (checkError) {
    return (
      <div className="auth-shell">
        <div className="w-full max-w-sm card p-7 text-center space-y-3 animate-fade-in">
          <div style={{ color: "var(--failed)" }}><Icon name="alert" size={30} /></div>
          <h1 className="font-semibold">Link not usable</h1>
          <p className="text-sm text-mid">{checkError}</p>
          <a href="/login" className="btn btn-ghost w-full py-2.5 text-sm mt-2">Back to sign in</a>
        </div>
      </div>
    );
  }

  if (invitee && !verified) {
    return (
      <div className="auth-shell">
        <form onSubmit={verifyCode} className="auth-card card p-7 space-y-5 animate-fade-in">
          <div className="auth-mark">
            <Icon name="shield" size={20} className="text-accent" /> WhipGuard
          </div>
          <p className="text-sm text-lo -mt-3">
            Welcome, <b className="text-mid">{invitee.name}</b>. First, confirm it is really you: we will email a 6-digit code to{" "}
            <b className="text-mid">{invitee.email}</b>.
          </p>
          {!sentTo ? (
            <button type="button" onClick={sendCode} disabled={codeBusy} className="btn btn-primary w-full py-2.5 text-sm">
              {codeBusy ? "Sending…" : "Email me a code"}
            </button>
          ) : (
            <div className="space-y-3">
              <p className="text-sm text-lo">Code sent to {sentTo}. It expires in 10 minutes.</p>
              <div>
                <label className="section-label mb-1.5 block">6-digit code</label>
                <input
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  autoFocus
                  required
                  maxLength={6}
                  value={code}
                  onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
                  placeholder="123456"
                  className="input w-full px-3 py-2.5 text-sm"
                />
              </div>
              <button type="submit" disabled={codeBusy || code.length !== 6} className="btn btn-primary w-full py-2.5 text-sm">
                {codeBusy ? "Checking…" : "Verify"}
              </button>
              <button type="button" onClick={sendCode} disabled={codeBusy} className="btn btn-ghost w-full py-2 text-xs">
                Send a new code
              </button>
            </div>
          )}
          {codeError && <p className="auth-error">{codeError}</p>}
        </form>
      </div>
    );
  }

  return (
    <div className="auth-shell">
      <form onSubmit={submit} className="auth-card card p-7 space-y-5 animate-fade-in">
        <div className="auth-mark">
          <Icon name="shield" size={20} className="text-accent" /> WhipGuard
        </div>
        {invitee ? (
          <p className="text-sm text-lo -mt-3">
            Welcome, <b className="text-mid">{invitee.name}</b>. Set a password for <b className="text-mid">{invitee.email}</b>.
          </p>
        ) : (
          <div className="space-y-2 -mt-1">
            <div className="skeleton h-4 w-3/4" />
          </div>
        )}

        <div className="space-y-3">
          <div>
            <label className="section-label mb-1.5 block">Password</label>
            <input
              type="password"
              autoFocus
              required
              minLength={8}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="At least 8 characters"
              className="input w-full px-3 py-2.5 text-sm"
            />
          </div>
          <div>
            <label className="section-label mb-1.5 block">Confirm password</label>
            <input
              type="password"
              required
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              placeholder="Same password again"
              className="input w-full px-3 py-2.5 text-sm"
            />
          </div>
        </div>

        {submitError && <p className="auth-error">{submitError}</p>}

        <button
          type="submit"
          disabled={loading || !invitee || password.length < 8 || !confirm}
          className="btn btn-primary w-full py-2.5 text-sm"
        >
          {loading ? "Setting up…" : "Create account"}
        </button>
      </form>
    </div>
  );
}
