"use client";

import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { usePathname, useRouter } from "next/navigation";
import { api, type SessionInfo } from "@/lib/api";
import { OnboardingModal } from "./OnboardingModal";

// The marketing landing page ("/"), login, and signup need no session at
// all -- everything else (the actual dashboard) is gated.
const PUBLIC_PATHS = ["/", "/login", "/signup", "/accept-invite"];

const SessionContext = createContext<SessionInfo>({ authenticated: false });

export function useSession() {
  return useContext(SessionContext);
}

export function AuthGate({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<"checking" | "authed" | "anon">("checking");
  const [session, setSession] = useState<SessionInfo>({ authenticated: false });
  const [onboardingDismissed, setOnboardingDismissed] = useState(false);
  const pathname = usePathname();
  const router = useRouter();
  const isPublic = PUBLIC_PATHS.includes(pathname);

  useEffect(() => {
    let cancelled = false;
    api
      .session()
      .then((s) => {
        if (cancelled) return;
        setSession(s);
        setStatus(s.authenticated ? "authed" : "anon");
        if (!s.authenticated && !isPublic) router.replace("/login");
      })
      .catch(() => {
        if (cancelled) return;
        setStatus("anon");
        if (!isPublic) router.replace("/login");
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pathname]);

  if (isPublic) return <SessionContext.Provider value={session}>{children}</SessionContext.Provider>;

  if (status !== "authed") {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="flex items-center gap-2.5 text-sm text-gray-500">
          <span className="dot bg-accent animate-pulse-dot" />
          {status === "checking" ? "Checking session…" : "Redirecting to sign in…"}
        </div>
      </div>
    );
  }
  const showOnboarding = !isPublic && session.authenticated && session.onboarding_completed === false && !onboardingDismissed;

  return (
    <SessionContext.Provider value={session}>
      {children}
      {showOnboarding && <OnboardingModal onDone={() => setOnboardingDismissed(true)} />}
    </SessionContext.Provider>
  );
}
