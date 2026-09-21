"use client";

/* ⌘K navigation. This dashboard has nine routes plus one detail page per
   issue, and reaching an issue previously meant loading the overview and
   scanning a list for it. The palette searches real issues by title, not
   just static routes, so the thing you are actually looking for is
   reachable from anywhere in two keystrokes.

   Issues are fetched once when the palette first opens, not on every
   keypress: the list is small, and a per-keystroke request would put a
   network round-trip between typing and seeing results. */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { api, type IssueSummary } from "@/lib/api";

type Command = { id: string; label: string; hint?: string; run: () => void };

const ROUTES: { label: string; href: string; hint: string }[] = [
  { label: "Overview", href: "/dashboard", hint: "Issues, confidence, pending approvals" },
  { label: "Repositories", href: "/repos", hint: "Watched repos, Slack routing, settings" },
  { label: "Team", href: "/org", hint: "Members, areas, levels and routing" },
  { label: "Live activity", href: "/activity", hint: "Streaming council events" },
  { label: "Connect", href: "/connect", hint: "Repos, GitHub and Slack channels" },
  { label: "Profile", href: "/profile", hint: "Your details and connected apps" },
  { label: "Admin", href: "/admin", hint: "Users, usage and redeploys" },
];

export function CommandPalette() {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [issues, setIssues] = useState<IssueSummary[]>([]);
  const [cursor, setCursor] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setOpen((current) => !current);
      }
      if (event.key === "Escape") setOpen(false);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    if (!open) {
      setQuery("");
      setCursor(0);
      return;
    }
    inputRef.current?.focus();
    if (issues.length === 0) api.issues().then(setIssues).catch(() => {});
  }, [open, issues.length]);

  const commands: Command[] = useMemo(() => {
    const go = (href: string) => () => {
      setOpen(false);
      router.push(href);
    };
    const routeCommands = ROUTES.map((route) => ({
      id: route.href,
      label: route.label,
      hint: route.hint,
      run: go(route.href),
    }));
    const issueCommands = issues.slice(0, 40).map((issue) => ({
      id: issue.id,
      label: issue.title,
      hint: `${issue.category} · ${issue.badge}`,
      run: go(`/issues/${issue.id}`),
    }));
    const all = [...routeCommands, ...issueCommands];
    const needle = query.trim().toLowerCase();
    if (!needle) return all.slice(0, 12);
    return all.filter((command) => `${command.label} ${command.hint ?? ""}`.toLowerCase().includes(needle)).slice(0, 12);
  }, [issues, query, router]);

  const onKeyDown = useCallback(
    (event: React.KeyboardEvent) => {
      if (event.key === "ArrowDown") {
        event.preventDefault();
        setCursor((current) => (current + 1) % Math.max(commands.length, 1));
      } else if (event.key === "ArrowUp") {
        event.preventDefault();
        setCursor((current) => (current - 1 + commands.length) % Math.max(commands.length, 1));
      } else if (event.key === "Enter") {
        event.preventDefault();
        commands[cursor]?.run();
      }
    },
    [commands, cursor],
  );

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center pt-[12vh] px-4"
      style={{ background: "rgba(4,5,6,0.72)", backdropFilter: "blur(3px)" }}
      onClick={() => setOpen(false)}
    >
      <div className="card w-full max-w-lg overflow-hidden animate-fade-in" onClick={(event) => event.stopPropagation()}>
        <input
          ref={inputRef}
          value={query}
          onChange={(event) => {
            setQuery(event.target.value);
            setCursor(0);
          }}
          onKeyDown={onKeyDown}
          placeholder="Jump to a page or an issue…"
          className="w-full bg-transparent px-4 py-3.5 text-sm outline-none border-b"
          style={{ borderColor: "var(--ink-700)" }}
        />
        <div className="max-h-80 overflow-y-auto py-1.5">
          {commands.length === 0 && <p className="px-4 py-6 text-center text-xs text-lo">No matches.</p>}
          {commands.map((command, index) => (
            <button
              key={command.id}
              onMouseEnter={() => setCursor(index)}
              onClick={command.run}
              className="w-full text-left px-4 py-2.5 flex items-center justify-between gap-3 transition"
              style={{ background: index === cursor ? "rgba(255,178,36,0.09)" : "transparent" }}
            >
              <span className="text-sm truncate text-hi min-w-0">{command.label}</span>
              {command.hint && <span className="text-[11px] text-lo shrink-0">{command.hint}</span>}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
