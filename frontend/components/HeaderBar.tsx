"use client";

import { useEffect, useState } from "react";
import { Icon } from "@/components/Icon";
import { usePathname, useRouter } from "next/navigation";
import { api, type GithubProfile } from "@/lib/api";
import { releasePushBinding } from "@/lib/push";
import { useSession } from "./AuthGate";

const NAV_LINKS = [
  { href: "/dashboard", label: "Overview" },
  { href: "/repos", label: "Repos" },
  { href: "/org", label: "Team" },
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
  const [online, setOnline] = useState(true);

  useEffect(() => {
    if (HIDDEN_ON.includes(pathname)) return;
    api.githubProfile().then(setProfile).catch(() => {});
  }, [pathname]);

  useEffect(() => {
    if (HIDDEN_ON.includes(pathname)) return;
    let cancelled = false;
    const ping = async () => {
      try {
        const res = await fetch("/api/healthz", { cache: "no-store" });
        if (!cancelled) setOnline(res.ok);
      } catch {
        if (!cancelled) setOnline(false);
      }
    };
    ping();
    const timer = setInterval(ping, 15000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [pathname]);

  if (HIDDEN_ON.includes(pathname)) return null;

  async function logout() {
    // Drop the SERVER row for this device first, so it stops receiving the
    // account being signed out of. Deliberately not a browser-level
    // unsubscribe: that would destroy the subscription and force a fresh
    // permission grant, which iOS will not reliably re-prompt for once
    // dismissed. Keeping it lets the next login re-bind instantly.
    await releasePushBinding();
    await api.logout();
    router.push("/login");
  }

  return (
    <div
      className="border-b border-border bg-[color:var(--ink-800)] backdrop-blur-md sticky top-0 z-20"
      // Painting under the notch is what `viewportFit: "cover"` asks for; this
      // is the half that keeps the title out from under the status bar.
      style={{ paddingTop: "env(safe-area-inset-top)" }}
    >
      <div className="max-w-6xl mx-auto px-3 sm:px-4 py-2.5 sm:py-3 flex items-center justify-between gap-3 sm:gap-4">
        <div className="flex items-center gap-4 sm:gap-6 min-w-0">
          <a href="/dashboard" className="flex items-center gap-2 font-semibold tracking-tight shrink-0">
            <Icon name="shield" size={18} className="text-accent shrink-0" />
            <span className="truncate">WhipGuard</span>
          </a>
          <nav className="hidden sm:flex items-center gap-1 text-sm">
            {NAV_LINKS.map((link) => (
              <a
                key={link.href}
                href={link.href}
                className={`px-2.5 py-1.5 rounded-md transition ${
                  pathname === link.href ? "text-white bg-white/8" : "text-mid hover:text-hi hover:bg-white/5"
                }`}
              >
                {link.label}
              </a>
            ))}
            {session.role === "admin" && (
              <a
                href="/admin"
                className={`px-2.5 py-1.5 rounded-md transition ${
                  pathname.startsWith("/admin") ? "text-hi bg-white/[0.07]" : "text-lo hover:text-hi hover:bg-white/5"
                }`}
              >
                Admin
              </a>
            )}
          </nav>
        </div>

        <div className="flex items-center gap-3 text-sm text-mid">
          {/* Was a hardcoded "council running" pill that said the same thing
              whether or not anything was reachable. A status indicator that
              cannot be wrong is decoration; this one actually polls. */}
          <span className={`badge hidden sm:inline-flex ${online ? "badge-green" : "badge-red"}`}>
            <span
              className={`dot ${online ? "" : "animate-pulse-dot"}`}
              style={{ background: online ? "var(--verified)" : "var(--failed)" }}
            />
            {online ? "connected" : "reconnecting"}
          </span>
          {profile?.connected && (
            <a
              href={profile.html_url}
              target="_blank"
              rel="noreferrer"
              className="hidden sm:flex items-center gap-1.5 hover:text-hi"
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
              className="w-9 h-9 sm:w-7 sm:h-7 rounded-full text-xs font-semibold flex items-center justify-center transition num shrink-0"
              style={{ background: "rgba(255,178,36,0.14)", border: "1px solid var(--amber-dim)", color: "var(--amber)" }}
              aria-label="Account menu"
            >
              {session.email?.[0]?.toUpperCase() ?? "?"}
            </button>
            {menuOpen && (
              <>
                <div className="fixed inset-0 z-10" onClick={() => setMenuOpen(false)} />
                <div className="absolute right-0 top-9 z-20 w-56 card p-1.5 animate-fade-in">
                  <div className="px-2.5 py-2 text-xs text-lo truncate border-b mb-1" style={{ borderColor: "var(--ink-700)" }}>
                    {session.email}
                    {session.role === "admin" && <span className="badge badge-accent ml-1.5">admin</span>}
                  </div>
                  <a
                    href="/profile"
                    className="block w-full text-left px-2.5 py-1.5 rounded-md text-sm text-mid hover:bg-white/5 hover:text-hi transition tap-target flex items-center"
                  >
                    Profile
                  </a>
                  {/* The bottom tab bar holds five destinations and these are
                      not among them, so on a phone this menu is the only way
                      to reach either. Hidden from `sm` up, where the header's
                      own nav already carries them. */}
                  <a
                    href="/connect"
                    className="sm:hidden w-full text-left px-2.5 py-1.5 rounded-md text-sm text-mid hover:bg-white/5 hover:text-hi transition tap-target flex items-center"
                  >
                    Connect a repo
                  </a>
                  {session.role === "admin" && (
                    <a
                      href="/admin"
                      className="sm:hidden w-full text-left px-2.5 py-1.5 rounded-md text-sm text-mid hover:bg-white/5 hover:text-hi transition tap-target flex items-center"
                    >
                      Admin
                    </a>
                  )}
                  <button
                    onClick={logout}
                    className="w-full text-left px-2.5 py-1.5 rounded-md text-sm text-mid hover:bg-white/5 hover:text-hi transition tap-target flex items-center"
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
