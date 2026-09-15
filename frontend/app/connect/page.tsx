"use client";

import { useEffect, useState } from "react";
import { api, type GithubProfile, type GithubRepo } from "@/lib/api";

export default function ConnectPage() {
  const [profile, setProfile] = useState<GithubProfile | null>(null);
  const [repos, setRepos] = useState<GithubRepo[]>([]);
  const [repoIds, setRepoIds] = useState<Record<string, string>>({});
  const [connecting, setConnecting] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Read directly off window instead of next/navigation's useSearchParams --
  // that hook forces this page into a Suspense boundary at build time for no
  // benefit here, since this is a client-only redirect-landing read.
  const [oauthError, setOauthError] = useState<string | null>(null);

  useEffect(() => {
    setOauthError(new URLSearchParams(window.location.search).get("github_error"));
  }, []);

  async function refresh() {
    try {
      const [p, r, connected] = await Promise.all([api.githubProfile(), api.githubRepos(), api.repos()]);
      setProfile(p);
      setRepos(r);
      setRepoIds(Object.fromEntries(connected.map((c) => [c.github_full_name, c.id])));
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
    <div className="space-y-8 animate-fade-in">
      <div>
        <h1 className="text-xl font-semibold">Connect</h1>
        <p className="text-sm text-gray-500 mt-1">Link GitHub and choose which repos WhipGuard watches.</p>
      </div>

      <section className="card p-5">
        <h2 className="font-semibold mb-3">GitHub</h2>
        {error && <p className="text-sm text-red-400 mb-3">{error}</p>}
        {oauthError && <p className="text-sm text-red-400 mb-3">GitHub sign-in failed: {oauthError}</p>}
        {profile?.connected ? (
          <div className="flex items-center gap-3">
            {profile.avatar_url && (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={profile.avatar_url} alt="" className="w-10 h-10 rounded-full ring-1 ring-border" />
            )}
            <div>
              <div className="font-medium">{profile.name || profile.login}</div>
              <a href={profile.html_url} target="_blank" rel="noreferrer" className="text-xs text-gray-500 hover:text-accent-soft">
                @{profile.login}
              </a>
            </div>
            <span className="badge badge-green ml-auto">Connected</span>
          </div>
        ) : (
          <div className="flex items-center justify-between">
            <p className="text-sm text-gray-500">Not connected to GitHub yet.</p>
            <a href="/api/github/oauth/start" className="btn btn-primary px-4 py-2 text-sm">
              Connect to GitHub
            </a>
          </div>
        )}
      </section>

      <section>
        <h2 className="section-label mb-3">Repositories</h2>
        <div className="space-y-2">
          {repos.map((repo) => (
            <div key={repo.full_name} className="card card-hover px-4 py-3 flex items-center justify-between">
              <div>
                <a href={repo.html_url} target="_blank" rel="noreferrer" className="font-medium text-sm hover:underline">
                  {repo.full_name}
                </a>
                <div className="text-xs text-gray-500 mt-0.5">
                  {repo.private ? "private" : "public"} · default branch {repo.default_branch}
                </div>
              </div>
              {repo.connected ? (
                <div className="flex items-center gap-2">
                  <span className="badge badge-green">Connected</span>
                  {repoIds[repo.full_name] && (
                    <a href={`/repos/${repoIds[repo.full_name]}/settings`} className="btn btn-ghost px-3 py-1.5 text-xs">
                      Settings
                    </a>
                  )}
                </div>
              ) : (
                <button
                  onClick={() => connect(repo.full_name)}
                  disabled={connecting === repo.full_name}
                  className="btn btn-ghost px-3 py-1.5 text-xs"
                >
                  {connecting === repo.full_name ? "Connecting…" : "Connect"}
                </button>
              )}
            </div>
          ))}
          {repos.length === 0 && !error && (
            <div className="card px-4 py-8 text-center text-gray-500 text-sm">Loading repositories…</div>
          )}
        </div>
      </section>
    </div>
  );
}
