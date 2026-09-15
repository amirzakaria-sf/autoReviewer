"use client";

import { useEffect, useState, type ReactNode } from "react";
import { usePathname, useRouter } from "next/navigation";
import { api } from "@/lib/api";

// The marketing landing page ("/") and the login form need no session at
// all -- everything else (the actual dashboard) is gated.
const PUBLIC_PATHS = ["/", "/login"];

export function AuthGate({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<"checking" | "authed" | "anon">("checking");
  const pathname = usePathname();
  const router = useRouter();
  const isPublic = PUBLIC_PATHS.includes(pathname);

  useEffect(() => {
    let cancelled = false;
    api.session().then((s) => {
      if (cancelled) return;
      setStatus(s.authenticated ? "authed" : "anon");
      if (!s.authenticated && !isPublic) router.replace("/login");
    });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pathname]);

  if (isPublic) return <>{children}</>;
  if (status !== "authed") {
    return (
      <div className="min-h-screen flex items-center justify-center text-gray-500 text-sm">
        {status === "checking" ? "Checking session…" : "Redirecting to sign in…"}
      </div>
    );
  }
  return <>{children}</>;
}
