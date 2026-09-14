"use client";

import { useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { api, type GithubProfile } from "@/lib/api";

export function HeaderBar() {
  const pathname = usePathname();
  const router = useRouter();
  const [profile, setProfile] = useState<GithubProfile | null>(null);

  useEffect(() => {
    if (pathname === "/login") return;
    api.githubProfile().then(setProfile).catch(() => {});
  }, [pathname]);

  if (pathname === "/login") return null;

  async function logout() {
    await api.logout();
    router.push("/login");
  }

  return (
    <div className="border-b border-border bg-panel/60 backdrop-blur sticky top-0 z-10">
      <div className="max-w-6xl mx-auto px-4 py-3 flex items-center justify-between">
        <a href="/" className="flex items-center gap-2 font-semibold tracking-tight">
          <span className="text-lg">🛡️</span> WhipGuard
        </a>
        <div className="flex items-center gap-4 text-sm text-gray-400">
          <a href="/" className="hover:text-white">Overview</a>
          <a href="/activity" className="hover:text-white">Live activity</a>
          <a href="/connect" className="hover:text-white">Connect</a>
          <span className="badge badge-green">detection: on</span>
          {profile?.connected && (
            <a
              href={profile.html_url}
              target="_blank"
              className="flex items-center gap-1.5 hover:text-white"
              title={`Connected to GitHub as ${profile.login}`}
            >
              {profile.avatar_url && (
                // eslint-disable-next-line @next/next/no-img-element
                <img src={profile.avatar_url} alt="" className="w-5 h-5 rounded-full" />
              )}
              <span>{profile.login}</span>
            </a>
          )}
          <button onClick={logout} className="hover:text-white">
            Sign out
          </button>
        </div>
      </div>
    </div>
  );
}
