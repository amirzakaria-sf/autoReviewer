"use client";

import { useMemo, useState } from "react";

/** How many lines render before the view collapses itself.
 *  A 900-line diff pushes the conversation and the decision buttons off the
 *  screen entirely, which is the one thing this page exists to show. */
const COLLAPSE_AFTER = 40;

type Line = { text: string; kind: "add" | "del" | "meta" | "hunk" | "ctx" };

function classify(line: string): Line["kind"] {
  if (line.startsWith("+++") || line.startsWith("---") || line.startsWith("diff ") || line.startsWith("index "))
    return "meta";
  if (line.startsWith("@@")) return "hunk";
  if (line.startsWith("+")) return "add";
  if (line.startsWith("-")) return "del";
  return "ctx";
}

const TONE: Record<Line["kind"], { color: string; background?: string }> = {
  add: { color: "var(--verified)", background: "rgba(47,212,143,0.07)" },
  del: { color: "#ff8b84", background: "rgba(255,95,86,0.07)" },
  hunk: { color: "var(--amber)" },
  meta: { color: "var(--text-lo)" },
  ctx: { color: "var(--text-mid)" },
};

export function DiffView({ diff }: { diff: string }) {
  const [expanded, setExpanded] = useState(false);

  const lines = useMemo<Line[]>(
    () => diff.split("\n").map((text) => ({ text, kind: classify(text) })),
    [diff],
  );

  const counts = useMemo(() => {
    let added = 0;
    let removed = 0;
    for (const line of lines) {
      if (line.kind === "add") added += 1;
      if (line.kind === "del") removed += 1;
    }
    return { added, removed };
  }, [lines]);

  if (!diff.trim()) {
    return (
      <p className="text-xs text-lo">
        No diff was recorded for this attempt — it scored below the threshold and never produced a patch.
      </p>
    );
  }

  const shown = expanded ? lines : lines.slice(0, COLLAPSE_AFTER);
  const hidden = lines.length - shown.length;

  return (
    <div>
      <div className="flex items-center gap-3 mb-2">
        <span className="section-label">Proposed diff</span>
        <span className="num text-[11px]" style={{ color: "var(--verified)" }}>
          +{counts.added}
        </span>
        <span className="num text-[11px]" style={{ color: "#ff8b84" }}>
          −{counts.removed}
        </span>
      </div>
      <pre
        className="text-[11px] leading-[1.6] rounded-lg overflow-x-auto num"
        style={{ background: "var(--ink-900)", border: "1px solid var(--ink-700)", padding: "10px 0" }}
      >
        {shown.map((line, index) => (
          <div
            key={index}
            style={{
              color: TONE[line.kind].color,
              background: TONE[line.kind].background,
              padding: "0 14px",
              whiteSpace: "pre",
            }}
          >
            {line.text || " "}
          </div>
        ))}
      </pre>
      {hidden > 0 && (
        <button onClick={() => setExpanded(true)} className="btn btn-ghost mt-2 px-3 py-1 text-xs">
          Show {hidden} more line{hidden === 1 ? "" : "s"}
        </button>
      )}
      {expanded && lines.length > COLLAPSE_AFTER && (
        <button onClick={() => setExpanded(false)} className="btn btn-ghost mt-2 px-3 py-1 text-xs">
          Collapse
        </button>
      )}
    </div>
  );
}
