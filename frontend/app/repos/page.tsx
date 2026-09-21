"use client";

/* Every connected repo, with the things you actually act on per repo: whether
   detection is paused, which Slack channel it reports to, and a way into its
   settings. This page did not exist -- `/repos/[id]/settings` was reachable
   only by typing the URL, which is why "manage channels" led nowhere useful. */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { EmptyState, RowSkeleton } from "@/components/EmptyState";

type ConnectedRepo = Awaited<ReturnType<typeof api.repos>>[number];

export default function ReposPage() {
  const [repos, setRepos] = useState<ConnectedRepo[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setRepos(await api.repos());
      setError(null);
    } catch {
      setError("Could not load your repositories.");
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return (
    <div className="space-y-6 animate-fade-in">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-lg font-semibold">Repositories</h1>
          <p className="text-xs text-lo mt-0.5">Everything WhipGuard watches. Slack notifications for all of them go to the one channel set on your profile.</p>
        </div>
        <Link href="/connect" className="btn btn-primary px-4 py-2">
          Connect a repo
        </Link>
      </div>

      {error && <p className="badge badge-red">{error}</p>}

      {repos === null && <RowSkeleton rows={2} />}

      {repos?.length === 0 && (
        <EmptyState
          title="No repositories connected yet"
          hint="Connect a GitHub repo and WhipGuard will start scanning it for bugs it can evidence, score, and fix."
          action={
            <Link href="/connect" className="btn btn-primary px-4 py-2">
              Connect a repo
            </Link>
          }
        />
      )}

      <div className="space-y-2.5">
        {repos?.map((repo) => {
          const paused = repo.detection_paused || repo.proposals_paused;
          return (
            <div key={repo.id} className="card card-hover p-4 flex items-center justify-between gap-4">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <span className="dot" style={{ background: paused ? "var(--amber)" : "var(--verified)" }} />
                  <span className="font-medium text-sm truncate min-w-0">{repo.github_full_name}</span>
                </div>
                <div className="mt-2 flex flex-wrap items-center gap-1.5">
                  <span className="badge badge-gray num">{repo.default_branch}</span>
                  {repo.detection_paused && <span className="badge badge-amber">Detection paused</span>}
                  {repo.proposals_paused && <span className="badge badge-amber">Proposals paused</span>}
                </div>
              </div>
              <Link href={`/repos/${repo.id}/settings`} className="btn btn-ghost px-3.5 py-2 shrink-0">
                Settings
              </Link>
            </div>
          );
        })}
      </div>
    </div>
  );
}
