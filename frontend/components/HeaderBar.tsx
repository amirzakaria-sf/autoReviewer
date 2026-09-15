"use client";

import { useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { api, type GithubProfile } from "@/lib/api";
import { useSession } from "./AuthGate";

const NAV_LINKS = [
  { href: "/dashboard", label: "Overview" },
  { href: "/activity", label: "Live activity" },
  { href: "/connect", label: "Connect" },
];

const HIDDEN_ON = ["/login", "/signup", "/accept-invite", "/"];

export function HeaderBar() {
  const pathname = usePathname();
  const router = useRouter();
  const session = useSession();
  const [profile, setProfile] = useState<GithubProfile | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);

  useEffect(() => {
    if (HIDDEN_ON.includes(pathname)) return;
    api.githubProfile().then(setProfile).catch(() => {});
  }, [pathname]);

  if (HIDDEN_ON.includes(pathname)) return null;

  async function logout() {
    await api.logout();
    router.push("/login");
  }

  return (
    <div className="border-b border-border bg-panel/70 backdrop-blur-md sticky top-0 z-20">
      <div className="max-w-6xl mx-auto px-4 py-3 flex items-center justify-between gap-4">
        <div className="flex items-center gap-6">
          <a href="/dashboard" className="flex items-center gap-2 font-semibold tracking-tight shrink-0">
            <span className="text-lg">🛡️</span> WhipGuard
          </a>
          <nav className="hidden sm:flex items-center gap-1 text-sm">
            {NAV_LINKS.map((link) => (
              <a
                key={link.href}
                href={link.href}
                className={`px-2.5 py-1.5 rounded-md transition ${
                  pathname === link.href ? "text-white bg-white/8" : "text-gray-400 hover:text-white hover:bg-white/5"
                }`}
              >
                {link.label}
              </a>
            ))}
            {session.role === "admin" && (
              <a
                href="/admin"
                className={`px-2.5 py-1.5 rounded-md transition ${
                  pathname.startsWith("/admin") ? "text-white bg-white/8" : "text-gray-400 hover:text-white hover:bg-white/5"
                }`}
              >
                Admin
              </a>
            )}
          </nav>
        </div>

        <div className="flex items-center gap-3 text-sm text-gray-400">
          <span className="badge badge-green hidden sm:inline-flex">
            <span className="dot bg-green-400" /> council running
          </span>
          {profile?.connected && (
            <a
              href={profile.html_url}
              target="_blank"
              rel="noreferrer"
              className="hidden sm:flex items-center gap-1.5 hover:text-white"
              title={`Connected to GitHub as ${profile.login}`}
            >
              {profile.avatar_url && (
                // eslint-disable-next-line @next/next/no-img-element
                <img src={profile.avatar_url} alt="" className="w-5 h-5 rounded-full" />
              )}
            </a>
          )}

          <div className="relative">
            <button
              onClick={() => setMenuOpen((v) => !v)}
              className="w-7 h-7 rounded-full bg-accent/20 border border-accent/40 text-accent-soft text-xs font-semibold flex items-center justify-center hover:bg-accent/30 transition"
            >
              {session.email?.[0]?.toUpperCase() ?? "?"}
            </button>
            {menuOpen && (
              <>
                <div className="fixed inset-0 z-10" onClick={() => setMenuOpen(false)} />
                <div className="absolute right-0 top-9 z-20 w-56 card p-1.5 animate-fade-in">
                  <div className="px-2.5 py-2 text-xs text-gray-500 truncate border-b border-border mb-1">
                    {session.email}
                    {session.role === "admin" && <span className="badge badge-accent ml-1.5">admin</span>}
                  </div>
                  <a
                    href="/profile"
                    className="block w-full text-left px-2.5 py-1.5 rounded-md text-sm text-gray-300 hover:bg-white/5 hover:text-white transition"
                  >
                    Profile
                  </a>
                  <button
                    onClick={logout}
                    className="w-full text-left px-2.5 py-1.5 rounded-md text-sm text-gray-300 hover:bg-white/5 hover:text-white transition"
                  >
                    Sign out
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
