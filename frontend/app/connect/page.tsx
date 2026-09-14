"use client";

import { useEffect, useState } from "react";
import { api, type GithubProfile, type GithubRepo } from "@/lib/api";

export default function ConnectPage() {
  const [profile, setProfile] = useState<GithubProfile | null>(null);
  const [repos, setRepos] = useState<GithubRepo[]>([]);
  const [connecting, setConnecting] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function refresh() {
    try {
      const [p, r] = await Promise.all([api.githubProfile(), api.githubRepos()]);
      setProfile(p);
      setRepos(r);
      setError(null);
    } catch {
      setError("Could not reach GitHub via the server's stored token.");
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  async function connect(fullName: string) {
    setConnecting(fullName);
    await api.connectRepo(fullName);
    await refresh();
    setConnecting(null);
  }

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold">Connect</h1>

      <section className="border border-border bg-panel rounded-lg p-4">
        <h2 className="font-semibold mb-3">GitHub</h2>
        {error && <p className="text-sm text-red-400">{error}</p>}
        {profile?.connected ? (
          <div className="flex items-center gap-3">
            {profile.avatar_url && (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={profile.avatar_url} alt="" className="w-10 h-10 rounded-full" />
            )}
            <div>
              <div className="font-medium">{profile.name || profile.login}</div>
              <a href={profile.html_url} target="_blank" className="text-xs text-gray-400 underline">
                @{profile.login}
              </a>
            </div>
            <span className="badge badge-green ml-auto">Connected</span>
          </div>
        ) : (
          <p className="text-sm text-gray-500">
            Not connected — set <code className="text-xs">GITHUB_TOKEN</code> in the server's environment.
          </p>
        )}
      </section>

      <section>
        <h2 className="font-semibold mb-3">Repositories</h2>
        <div className="border border-border rounded-lg divide-y divide-border">
          {repos.map((repo) => (
            <div key={repo.full_name} className="px-4 py-3 flex items-center justify-between">
              <div>
                <a href={repo.html_url} target="_blank" className="font-medium hover:underline">
                  {repo.full_name}
                </a>
                <div className="text-xs text-gray-500">
                  {repo.private ? "private" : "public"} · default branch {repo.default_branch}
                </div>
              </div>
              {repo.connected ? (
                <span className="badge badge-green">Connected</span>
              ) : (
                <button
                  onClick={() => connect(repo.full_name)}
                  disabled={connecting === repo.full_name}
                  className="text-xs px-3 py-1.5 rounded-md bg-white/10 hover:bg-white/20 disabled:opacity-50"
                >
                  {connecting === repo.full_name ? "Connecting…" : "Connect"}
                </button>
              )}
            </div>
          ))}
          {repos.length === 0 && !error && (
            <div className="px-4 py-6 text-center text-gray-500 text-sm">Loading repositories…</div>
          )}
        </div>
      </section>
    </div>
  );
}
