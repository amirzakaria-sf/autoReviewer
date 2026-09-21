"use client";

import type { ReactNode } from "react";
import { usePathname } from "next/navigation";
import { PUBLIC_PATHS } from "./AuthGate";

/* The app's content container -- and deliberately NOT applied to the public
   pages.

   It used to wrap every route, which meant the marketing page's own
   full-bleed navigation bar was rendered inside a centred, padded box: at
   1440px it came out 1120px wide, inset 160px on each side and pushed 28px
   down the page, so a bar meant to span the window read as a floating
   rectangle that had failed to stretch.

   The bottom padding is the other half of the reason. It exists to clear the
   mobile tab bar and Counsel's launcher, neither of which a signed-out
   visitor ever sees. */
export function AppMain({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  if (PUBLIC_PATHS.includes(pathname)) return <>{children}</>;

  return <main className="max-w-6xl mx-auto px-4 py-5 pb-36 sm:py-7 sm:pb-7">{children}</main>;
}
