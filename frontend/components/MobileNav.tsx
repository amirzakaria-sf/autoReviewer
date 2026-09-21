"use client";

import { usePathname } from "next/navigation";

/* The header's nav is `hidden sm:flex` and always has been, with nothing
   behind it -- so on a phone this app had no navigation at all. You could
   reach the dashboard and then only move by editing the URL.

   A bottom tab bar rather than a hamburger: this is installed to a home
   screen and held one-handed, where the top of the screen is the hardest
   place to reach and a menu that must be opened before it can be read costs a
   tap on every single navigation. Five destinations is the most that stays
   legible at 360px. */

const TABS = [
  {
    href: "/dashboard",
    label: "Overview",
    icon: (
      <path d="M3 12l9-8 9 8M5 10v10h5v-6h4v6h5V10" />
    ),
  },
  {
    href: "/repos",
    label: "Repos",
    icon: (
      <>
        <path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H19v14H6.5A2.5 2.5 0 0 0 4 19.5z" />
        <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H19v4H6.5A2.5 2.5 0 0 1 4 19.5z" />
      </>
    ),
  },
  {
    href: "/activity",
    label: "Activity",
    icon: <path d="M3 12h4l2.5-7 5 14L17 12h4" />,
  },
  {
    href: "/org",
    label: "Team",
    icon: (
      <>
        <circle cx="9" cy="8" r="3.2" />
        <path d="M3 20c0-3.3 2.7-5.5 6-5.5s6 2.2 6 5.5" />
        <path d="M16 11.2A3 3 0 1 0 16 5.3M17.5 14.8c2.1.6 3.5 2.4 3.5 5.2" />
      </>
    ),
  },
  {
    href: "/profile",
    label: "You",
    icon: (
      <>
        <circle cx="12" cy="8" r="3.4" />
        <path d="M5 20c0-3.6 3.1-6 7-6s7 2.4 7 6" />
      </>
    ),
  },
];

const HIDDEN_ON = ["/login", "/signup", "/accept-invite", "/join", "/"];

export function MobileNav() {
  const pathname = usePathname();
  if (HIDDEN_ON.includes(pathname)) return null;

  return (
    <nav className="mobile-nav" aria-label="Primary">
      {TABS.map((tab) => {
        // Prefix match so /repos/<id>/settings keeps Repos lit -- a tab bar
        // that goes dark as soon as you open a detail page reads as though
        // you have left the section you are plainly still inside.
        const active = pathname === tab.href || pathname.startsWith(`${tab.href}/`);
        return (
          <a key={tab.href} href={tab.href} data-active={active} aria-current={active ? "page" : undefined}>
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth={1.8}
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
            >
              {tab.icon}
            </svg>
            {tab.label}
          </a>
        );
      })}
    </nav>
  );
}
