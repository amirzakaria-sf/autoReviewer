"use client";

import { useState } from "react";
import { api } from "@/lib/api";

export default function RequestAccessPage() {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<"pending" | "approved" | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const res = await api.requestAccess(name.trim(), email.trim().toLowerCase(), reason.trim());
      if (res.status === "approved" && res.invite_token) {
        window.location.href = `/accept-invite?token=${encodeURIComponent(res.invite_token)}`;
        return;
      }
      setResult("pending");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not send your request.");
    } finally {
      setLoading(false);
    }
  }

  if (result === "pending") {
    return (
      <div className="min-h-screen flex items-center justify-center px-4 bg-grid-fade">
        <div className="w-full max-w-sm card p-7 text-center space-y-3 animate-fade-in">
          <div className="text-3xl">✋</div>
          <h1 className="font-semibold">Request sent</h1>
          <p className="text-sm text-gray-400">
            An admin will review your request. If it's approved, you'll get an email at <b>{email}</b> with a link to set up your account.
          </p>
          <a href="/login" className="btn btn-ghost w-full py-2.5 text-sm mt-2">Back to sign in</a>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen flex items-center justify-center px-4 bg-grid-fade">
      <form onSubmit={submit} className="w-full max-w-sm card p-7 space-y-5 animate-fade-in">
        <div className="flex items-center gap-2.5 text-lg font-semibold">
          <span className="text-xl">🛡️</span> WhipGuard
        </div>
        <p className="text-sm text-gray-500 -mt-3">
          Request access. An admin reviews every request — no account is created until it's approved.
        </p>

        <div className="space-y-3">
          <div>
            <label className="section-label mb-1.5 block">Name</label>
            <input
              type="text"
              autoFocus
              required
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Ada Lovelace"
              className="input w-full px-3 py-2.5 text-sm"
            />
          </div>
          <div>
            <label className="section-label mb-1.5 block">Email</label>
            <input
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@company.com"
              className="input w-full px-3 py-2.5 text-sm"
            />
          </div>
          <div>
            <label className="section-label mb-1.5 block">Reason for access</label>
            <textarea
              required
              rows={3}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="What repo are you connecting, and why do you need WhipGuard on it?"
              className="input w-full px-3 py-2.5 text-sm resize-none"
            />
          </div>
        </div>

        {error && <p className="text-sm text-red-400">{error}</p>}

        <button
          type="submit"
          disabled={loading || !name || !email || !reason}
          className="btn btn-primary w-full py-2.5 text-sm"
        >
          {loading ? "Sending…" : "Request access"}
        </button>

        <p className="text-center text-sm text-gray-500">
          Already have an account? <a href="/login" className="text-accent-soft hover:underline">Sign in</a>
        </p>
      </form>
    </div>
  );
}
