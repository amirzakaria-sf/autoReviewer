"use client";

import { useEffect, useState, type ReactNode } from "react";
import { usePathname, useRouter } from "next/navigation";
import { api } from "@/lib/api";

export function AuthGate({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<"checking" | "authed" | "anon">("checking");
  const pathname = usePathname();
  const router = useRouter();

  useEffect(() => {
    let cancelled = false;
    api.session().then((s) => {
      if (cancelled) return;
      setStatus(s.authenticated ? "authed" : "anon");
      if (!s.authenticated && pathname !== "/login") router.replace("/login");
    });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pathname]);

  if (pathname === "/login") return <>{children}</>;
  if (status !== "authed") {
    return (
      <div className="min-h-screen flex items-center justify-center text-gray-500 text-sm">
        {status === "checking" ? "Checking session…" : "Redirecting to sign in…"}
      </div>
    );
  }
  return <>{children}</>;
}
