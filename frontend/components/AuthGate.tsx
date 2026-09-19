"use client";

import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { usePathname, useRouter } from "next/navigation";
import { OfflineError, api, startSessionKeepalive, type SessionInfo } from "@/lib/api";
import { OnboardingModal } from "./OnboardingModal";

// The marketing landing page ("/"), login, and signup need no session at
// all -- everything else (the actual dashboard) is gated.
// "/join" is here for the same reason as "/accept-invite": the person
// holding the link has no session yet -- that is the entire point of the
// link -- and bouncing them to /login loses the token.
const PUBLIC_PATHS = ["/", "/login", "/signup", "/accept-invite", "/join"];

const SessionContext = createContext<SessionInfo>({ authenticated: false });

export function useSession() {
  return useContext(SessionContext);
}

const OFFLINE_RETRY_MS = 2000;

export function AuthGate({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<"checking" | "authed" | "anon" | "offline">("checking");
  const [session, setSession] = useState<SessionInfo>({ authenticated: false });
  const [onboardingDismissed, setOnboardingDismissed] = useState(false);
  const pathname = usePathname();
  const router = useRouter();
  const isPublic = PUBLIC_PATHS.includes(pathname);
  const wasAuthed = useRef(false);

  useEffect(() => {
    let cancelled = false;
    let retry: ReturnType<typeof setTimeout> | undefined;

    async function check() {
      try {
        const s = await api.session();
        if (cancelled) return;
        setSession(s);
        if (s.authenticated) {
          wasAuthed.current = true;
          setStatus("authed");
          return;
        }
        setStatus("anon");
        if (!isPublic) router.replace("/login");
      } catch (error) {
        if (cancelled) return;
        // An unreachable backend is NOT a logout. This is the redeploy
        // window: the cookies are still valid, the server just isn't
        // answering yet, so hold the session and keep retrying instead of
        // throwing the user out to /login.
        if (error instanceof OfflineError) {
          setStatus(wasAuthed.current ? "authed" : "offline");
          retry = setTimeout(check, OFFLINE_RETRY_MS);
          return;
        }
        setStatus("anon");
        if (!isPublic) router.replace("/login");
      }
    }

    check();
    return () => {
      cancelled = true;
      if (retry) clearTimeout(retry);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pathname]);

  useEffect(() => startSessionKeepalive(), []);

  if (isPublic) return <SessionContext.Provider value={session}>{children}</SessionContext.Provider>;

  if (status !== "authed") {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="flex items-center gap-2.5 text-sm text-lo">
          <span className="dot bg-accent animate-pulse-dot" />
          {status === "checking" && "Checking session…"}
          {status === "offline" && "Reconnecting to WhipGuard…"}
          {status === "anon" && "Redirecting to sign in…"}
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
