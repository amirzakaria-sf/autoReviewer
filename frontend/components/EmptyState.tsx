"use client";

import type { ReactNode } from "react";

/* An empty list and a still-loading list previously looked identical: both
   rendered nothing. That ambiguity is why a working dashboard reads as
   broken on first load, so the two states are now visually distinct and the
   empty one says what to do about it. */

export function EmptyState({
  title,
  hint,
  action,
}: {
  title: string;
  hint?: string;
  action?: ReactNode;
}) {
  return (
    <div className="panel px-6 py-12 text-center">
      <div
        className="mx-auto mb-3 grid place-items-center rounded-full"
        style={{ width: 40, height: 40, background: "var(--ink-750)", border: "1px solid var(--ink-700)" }}
        aria-hidden
      >
        <span className="dot" style={{ background: "var(--text-lo)" }} />
      </div>
      <p className="text-sm font-medium text-hi">{title}</p>
      {hint && <p className="mt-1 text-xs text-lo max-w-sm mx-auto leading-relaxed">{hint}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export function RowSkeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div className="space-y-2" aria-hidden>
      {Array.from({ length: rows }).map((_, index) => (
        <div key={index} className="card px-4 py-3.5 flex items-center gap-3">
          <div className="skeleton" style={{ width: 34, height: 34, borderRadius: 999 }} />
          <div className="flex-1 space-y-2">
            <div className="skeleton h-3" style={{ width: `${55 + ((index * 13) % 30)}%` }} />
            <div className="skeleton h-2.5" style={{ width: `${28 + ((index * 17) % 22)}%` }} />
          </div>
        </div>
      ))}
    </div>
  );
}
