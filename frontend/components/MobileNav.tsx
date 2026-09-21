"use client";

import { usePathname } from "next/navigation";
import { Icon, type IconName } from "@/components/Icon";

/* The header's nav is `hidden sm:flex` and always has been, with nothing
   behind it -- so on a phone this app had no navigation at all. You could
   reach the dashboard and then only move by editing the URL.

   A bottom tab bar rather than a hamburger: this is installed to a home
   screen and held one-handed, where the top of the screen is the hardest
   place to reach and a menu that must be opened before it can be read costs a
   tap on every single navigation. Five destinations is the most that stays
   legible at 360px. */

const TABS: { href: string; label: string; icon: IconName }[] = [
  { href: "/dashboard", label: "Overview", icon: "home" },
  { href: "/repos", label: "Repos", icon: "repos" },
  { href: "/activity", label: "Activity", icon: "activity" },
  { href: "/org", label: "Team", icon: "team" },
  { href: "/profile", label: "You", icon: "user" },
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
            <Icon name={tab.icon} size={20} strokeWidth={1.7} />
            {tab.label}
          </a>
        );
      })}
    </nav>
  );
}
