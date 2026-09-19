"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";

export default function LoginPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const router = useRouter();

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.get("expired")) setNotice("Your session expired. Sign in again to continue.");
  }, []);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      await api.login(email.trim().toLowerCase(), password);
      router.push("/dashboard");
      router.refresh();
    } catch (err) {
      const message = err instanceof Error ? err.message : "";
      if (message.includes("awaiting admin approval")) {
        setError("Your account is awaiting admin approval. You'll get an email once it's reviewed.");
      } else if (message.includes("declined")) {
        setError("This access request was declined.");
      } else {
        setError("Incorrect email or password.");
      }
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="auth-shell">
      <form onSubmit={submit} className="auth-card card p-7 space-y-5 animate-fade-in">
        <div className="auth-mark">
          <span className="text-xl">🛡️</span> WhipGuard
        </div>
        <p className="text-sm text-lo -mt-3">Sign in to your bug council dashboard.</p>

        {notice && (
          <div className="auth-note">
            {notice}
          </div>
        )}

        <div className="space-y-3">
          <div>
            <label className="section-label mb-1.5 block">Email</label>
            <input
              type="email"
              autoFocus
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@company.com"
              className="input w-full px-3 py-2.5 text-sm"
            />
          </div>
          <div>
            <label className="section-label mb-1.5 block">Password</label>
            <input
              type="password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="••••••••"
              className="input w-full px-3 py-2.5 text-sm"
            />
          </div>
        </div>

        {error && <p className="auth-error">{error}</p>}

        <button type="submit" disabled={loading || !email || !password} className="btn btn-primary w-full py-2.5 text-sm">
          {loading ? "Signing in…" : "Sign in"}
        </button>

        <p className="text-center text-sm text-lo">
          No account? <a href="/signup" className="text-accent hover:underline">Request access</a>
        </p>
      </form>
    </div>
  );
}
