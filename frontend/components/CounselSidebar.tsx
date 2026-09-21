"use client";

/* Counsel's sidebar.
 *
 * The thing this component exists to avoid: a spinner, then a finished block
 * of text appearing all at once. That reads as broken even when it is
 * working, because the reader has no evidence anything is happening.
 *
 * So every step narrates itself. The server emits an event before each tool
 * runs, not after, and the answer arrives in fragments. The trail of steps
 * stays visible after the answer lands — it is the receipt showing which
 * files the answer was actually built from.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { usePathname } from "next/navigation";
import { Icon } from "@/components/Icon";
import { refreshSession } from "@/lib/api";

type Step =
  | { kind: "status"; text: string }
  | { kind: "tool"; label: string; detail: string; summary?: string; done: boolean };

type Job = {
  id: string;
  kind: string;
  status: "queued" | "running" | "done" | "failed";
  message: string;
  result: PrdResult | InvestigationResult | null;
};

type PrdResult = { title?: string; markdown?: string; coverage_score?: number; ungrounded?: string[] };
type InvestigationResult = { hypothesis?: string; where?: string; dispatched_categories?: string[]; note?: string };

type Turn = {
  id: string;
  question: string;
  steps: Step[];
  answer: string;
  citations: string[];
  jobs: Job[];
  error?: string;
  streaming: boolean;
};

const STORAGE_KEY = "whipguard.counsel.open";

const SUGGESTIONS = [
  "Explain how deleting an item works",
  "Who knows the most about app.js?",
  "What have we already tried and rejected?",
  "What breaks if I change deleteItem?",
];

export function CounselSidebar() {
  const [open, setOpen] = useState(false);
  const [input, setInput] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [busy, setBusy] = useState(false);
  const conversationId = useRef<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const pathname = usePathname();
  const isPublicRoute = ["/login", "/signup", "/accept-invite", "/join", "/"].includes(pathname);

  useEffect(() => {
    try {
      setOpen(localStorage.getItem(STORAGE_KEY) === "1");
    } catch {
      /* private window, or site data blocked — default closed */
    }
  }, []);

  const toggle = useCallback(() => {
    setOpen((current) => {
      const next = !current;
      try {
        localStorage.setItem(STORAGE_KEY, next ? "1" : "0");
      } catch {
        /* nothing to do — this is a convenience, not state */
      }
      return next;
    });
  }, []);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "j") {
        event.preventDefault();
        toggle();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [toggle]);

  // Worker-side jobs narrate over the shared activity socket, because the
  // worker has no connection to this browser of its own.
  useEffect(() => {
    // The early return that hides this component sits below the hooks, so
    // without this check the socket still opens on the landing and auth
    // pages -- where there is no session, the handshake is refused with
    // 4401, and the only visible result is a console error on every public
    // page load. Harmless before the socket required a session; not after.
    if (isPublicRoute) return;

    let socket: WebSocket | null = null;
    let closed = false;
    let refreshed = false;

    const open = () => {
      if (closed) return;
      socket = new WebSocket(
        `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/activity`,
      );
      wire(socket);
    };

    const wire = (target: WebSocket) => {
      target.onclose = async (event) => {
        // 4401 is the socket's own auth check rejecting an expired cookie.
        // Retried once, after a refresh: a job card that silently stops
        // updating looks exactly like a job that hung.
        if (closed || event.code !== 4401 || refreshed) return;
        refreshed = true;
        if (await refreshSession()) open();
      };
      target.onmessage = (message) => {
        try {
          const event = JSON.parse(message.data);
          if (event.type !== "counsel_job" || !event.job_id) return;
          setTurns((current) =>
            current.map((turn) => ({
              ...turn,
              jobs: turn.jobs.map((job) =>
                job.id === event.job_id ? { ...job, message: event.message, status: event.status } : job,
              ),
            })),
          );
        } catch {
          /* not ours */
        }
      };
    };

    open();
    return () => {
      closed = true;
      socket?.close();
    };
  }, [isPublicRoute]);

  // Follow the stream unless the reader has scrolled up to re-read something.
  useEffect(() => {
    const node = scrollRef.current;
    if (!node) return;
    const nearBottom = node.scrollHeight - node.scrollTop - node.clientHeight < 140;
    if (nearBottom) node.scrollTop = node.scrollHeight;
  }, [turns]);

  async function ask(question: string) {
    if (!question.trim() || busy) return;
    setBusy(true);
    setInput("");

    const turnId = `${Date.now()}`;
    setTurns((current) => [
      ...current,
      { id: turnId, question, steps: [], answer: "", citations: [], jobs: [], streaming: true },
    ]);

    const patch = (fn: (turn: Turn) => Turn) =>
      setTurns((current) => current.map((turn) => (turn.id === turnId ? fn(turn) : turn)));

    /* A PRD or investigation takes minutes, so it is a work item rather than
       a chat turn. Polled rather than streamed: the SSE response for THIS
       question closes as soon as Counsel finishes speaking, and holding it
       open for the length of a background job would tie up a connection to
       report on something the user may have scrolled away from. */
    const followJob = (owningTurn: string, jobId: string) => {
      let delay = 1500;
      const tick = async () => {
        try {
          const res = await fetch(`/api/counsel/jobs/${jobId}`, { credentials: "include", cache: "no-store" });
          if (res.ok) {
            const job = await res.json();
            setTurns((current) =>
              current.map((turn) =>
                turn.id !== owningTurn
                  ? turn
                  : {
                      ...turn,
                      jobs: turn.jobs.map((existing) =>
                        existing.id !== jobId
                          ? existing
                          : {
                              ...existing,
                              status: job.status,
                              result: job.result ?? existing.result,
                              message:
                                job.status === "done"
                                  ? "Complete"
                                  : job.status === "failed"
                                    ? job.error || "Failed"
                                    : existing.message,
                            },
                      ),
                    },
              ),
            );
            if (job.status === "done" || job.status === "failed") return;
          }
        } catch {
          /* transient — the next tick retries */
        }
        // Back off gradually: these run for minutes, and a 1.5s poll for all
        // of it is pure noise once the job is clearly long.
        delay = Math.min(delay * 1.4, 8000);
        setTimeout(tick, delay);
      };
      setTimeout(tick, delay);
    };

    try {
      const res = await fetch("/api/counsel/ask", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ question, conversation_id: conversationId.current }),
      });
      if (!res.ok || !res.body) throw new Error(`counsel responded ${res.status}`);

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        // SSE frames are separated by a blank line; the tail may be partial.
        const frames = buffer.split("\n\n");
        buffer = frames.pop() ?? "";

        for (const frame of frames) {
          const line = frame.split("\n").find((l) => l.startsWith("data: "));
          if (!line) continue;
          let event: Record<string, string & string[]>;
          try {
            event = JSON.parse(line.slice(6));
          } catch {
            continue;
          }

          if (event.type === "open") {
            conversationId.current = event.conversation_id as unknown as string;
          } else if (event.type === "status") {
            patch((turn) => ({ ...turn, steps: [...turn.steps, { kind: "status", text: event.text }] }));
          } else if (event.type === "tool") {
            patch((turn) => ({
              ...turn,
              steps: [
                ...turn.steps,
                { kind: "tool", label: event.label, detail: event.detail ?? "", done: false },
              ],
            }));
          } else if (event.type === "result") {
            patch((turn) => {
              const steps = [...turn.steps];
              for (let i = steps.length - 1; i >= 0; i--) {
                const step = steps[i];
                if (step.kind === "tool" && !step.done) {
                  steps[i] = { ...step, done: true, summary: event.summary };
                  break;
                }
              }
              return { ...turn, steps };
            });
          } else if (event.type === "token") {
            patch((turn) => ({ ...turn, answer: turn.answer + event.text }));
          } else if (event.type === "job") {
            const jobId = event.job_id as unknown as string;
            patch((turn) => ({
              ...turn,
              jobs: [
                ...turn.jobs,
                { id: jobId, kind: event.kind, status: "queued", message: "Queued", result: null },
              ],
            }));
            followJob(turnId, jobId);
          } else if (event.type === "done") {
            patch((turn) => ({
              ...turn,
              streaming: false,
              citations: (event.citations as unknown as string[]) ?? [],
            }));
          } else if (event.type === "error") {
            patch((turn) => ({ ...turn, streaming: false, error: event.text }));
          }
        }
      }
      patch((turn) => ({ ...turn, streaming: false }));
    } catch {
      patch((turn) => ({
        ...turn,
        streaming: false,
        error: "Lost the connection to Counsel. Nothing was changed — try again.",
      }));
    } finally {
      setBusy(false);
    }
  }

  if (isPublicRoute) return null;

  return (
    <>
      {!open && (
        <button
          onClick={toggle}
          className="btn btn-primary counsel-launcher fixed bottom-5 right-4 sm:right-5 z-40 shadow-glow"
          aria-label="Ask Counsel"
          title="Ask Counsel (⌘J)"
        >
          <Icon name="chat" size={22} strokeWidth={1.7} />
        </button>
      )}

      <aside
        className="counsel-panel fixed top-0 right-0 z-40 flex flex-col transition-transform duration-300"
        style={{
          width: "min(440px, 100vw)",
          transform: open ? "translateX(0)" : "translateX(100%)",
          background: "var(--ink-850)",
          borderLeft: "1px solid var(--ink-700)",
          boxShadow: open ? "-24px 0 48px -24px rgba(0,0,0,0.7)" : "none",
        }}
        aria-hidden={!open}
      >
        <header
          className="flex items-center justify-between px-4 py-3 shrink-0"
          style={{ borderBottom: "1px solid var(--ink-700)" }}
        >
          <div className="flex items-center gap-2">
            <span className="dot" style={{ background: "var(--amber)" }} />
            <span className="text-sm font-semibold">Counsel</span>
            <span className="text-[11px] text-lo">analyst · read-only</span>
          </div>
          <button onClick={toggle} className="btn btn-ghost px-2.5 py-1 text-xs" aria-label="Close Counsel">
            Close
          </button>
        </header>

        <div ref={scrollRef} className="flex-1 overflow-y-auto px-4 py-4 space-y-5">
          {turns.length === 0 && (
            <div className="space-y-3">
              <p className="text-sm text-mid leading-relaxed">
                I can explain how something works, trace what depends on it, tell you who knows an
                area, and check what WhipGuard already tried. I read the code — I never change it.
              </p>
              <div className="flex flex-col gap-1.5">
                {SUGGESTIONS.map((suggestion) => (
                  <button
                    key={suggestion}
                    onClick={() => ask(suggestion)}
                    className="text-left text-xs px-3 py-2 rounded-lg transition"
                    style={{ background: "var(--ink-800)", border: "1px solid var(--ink-700)" }}
                  >
                    {suggestion}
                  </button>
                ))}
              </div>
            </div>
          )}

          {turns.map((turn) => (
            <div key={turn.id} className="space-y-2.5 animate-fade-in">
              <div
                className="text-sm px-3 py-2 rounded-lg ml-6"
                style={{ background: "rgba(255,178,36,0.09)", border: "1px solid var(--amber-dim)" }}
              >
                {turn.question}
              </div>

              {turn.steps.length > 0 && <StepTrail steps={turn.steps} active={turn.streaming} />}

              {turn.answer && (
                <div className="text-sm leading-relaxed space-y-2.5">
                  <Markdown text={turn.answer} />
                  {turn.streaming && <span className="caret" aria-hidden />}
                </div>
              )}

              {turn.streaming && !turn.answer && turn.steps.length === 0 && (
                <div className="flex gap-1.5 py-1" aria-label="Counsel is thinking">
                  <span className="bounce" /> <span className="bounce" /> <span className="bounce" />
                </div>
              )}

              {turn.jobs.map((job) => (
                <JobCard key={job.id} job={job} />
              ))}

              {turn.citations.length > 0 && (
                <div className="flex flex-wrap gap-1.5 pt-1">
                  {turn.citations.map((citation) => (
                    <span key={citation} className="badge badge-gray num">
                      {citation}
                    </span>
                  ))}
                </div>
              )}

              {turn.error && <p className="text-xs" style={{ color: "var(--failed)" }}>{turn.error}</p>}
            </div>
          ))}
        </div>

        <form
          onSubmit={(event) => {
            event.preventDefault();
            ask(input);
          }}
          className="p-3 shrink-0 flex gap-2"
          style={{ borderTop: "1px solid var(--ink-700)" }}
        >
          <input
            id="counsel-input"
            value={input}
            onChange={(event) => setInput(event.target.value)}
            placeholder={busy ? "Counsel is working…" : "Ask about this codebase…"}
            disabled={busy}
            className="input flex-1 px-3 py-2 text-sm"
          />
          <button type="submit" disabled={busy || !input.trim()} className="btn btn-primary px-3.5 py-2">
            Ask
          </button>
        </form>
      </aside>
    </>
  );
}

/* A long job, rendered as a live card rather than a wait.
   A PRD takes minutes; the reader needs to see which of the three council
   roles is currently working, and then get the document inline without
   leaving the conversation. */
function JobCard({ job }: { job: Job }) {
  const [expanded, setExpanded] = useState(false);
  const running = job.status === "queued" || job.status === "running";
  const prd = job.kind === "counsel_prd" ? (job.result as PrdResult | null) : null;
  const investigation = job.kind === "counsel_investigate" ? (job.result as InvestigationResult | null) : null;

  const tone =
    job.status === "failed" ? "var(--failed)" : job.status === "done" ? "var(--verified)" : "var(--amber)";

  return (
    <div className="card p-3 space-y-2" style={{ borderColor: running ? "var(--amber-dim)" : undefined }}>
      <div className="flex items-center gap-2">
        {running ? <span className="spinner" style={{ marginTop: 0 }} aria-hidden /> : (
          <span className="dot" style={{ background: tone }} aria-hidden />
        )}
        <span className="text-xs font-semibold">
          {job.kind === "counsel_prd" ? "Feasibility council" : "Investigation"}
        </span>
        {prd?.coverage_score !== undefined && job.status === "done" && (
          <span className="badge badge-gray num ml-auto">coverage {prd.coverage_score}/100</span>
        )}
      </div>

      <p className="text-xs text-lo">{job.message}</p>

      {prd?.markdown && (
        <>
          <button onClick={() => setExpanded((v) => !v)} className="btn btn-ghost px-2.5 py-1 text-xs w-full">
            {expanded ? "Hide document" : `Read: ${prd.title ?? "the PRD"}`}
          </button>
          {expanded && (
            <div
              className="text-xs leading-relaxed max-h-80 overflow-y-auto p-2.5 rounded-lg space-y-2"
              style={{ background: "var(--ink-900)", border: "1px solid var(--ink-700)" }}
            >
              <Markdown text={prd.markdown} />
            </div>
          )}
          {prd.ungrounded && prd.ungrounded.length > 0 && (
            <p className="text-[11px]" style={{ color: "var(--failed)" }}>
              Not grounded in the codebase — treat as assertions: {prd.ungrounded.join("; ")}
            </p>
          )}
        </>
      )}

      {investigation && job.status === "done" && (
        <div className="text-xs text-mid space-y-1.5">
          {investigation.dispatched_categories && investigation.dispatched_categories.length > 0 && (
            <p>
              Asked the Bug Council to run:{" "}
              <span className="num">{investigation.dispatched_categories.join(", ")}</span>
            </p>
          )}
          <p className="text-lo">{investigation.note}</p>
        </div>
      )}
    </div>
  );
}

/* Just enough markdown for what Counsel actually writes: paragraphs, bullets,
   inline code, bold, italics and bare links. A full parser would be a
   dependency and a bundle for a handful of constructs, and the answer streams
   in fragments — so this has to tolerate half-finished syntax on every render,
   which a strict parser does not. Nothing here injects HTML; every branch
   renders React nodes.

   Links matter more here than they look: a PRD's research section cites the
   source every external claim was attributed to, and a citation the reader
   cannot follow is barely a citation. Rendered with rel="noreferrer" because
   these URLs come from a web search, not from us. */
function inline(text: string, keyPrefix: string) {
  const nodes: React.ReactNode[] = [];
  const pattern = /(`[^`]+`|\*\*[^*]+\*\*|_[^_\n]+_|https?:\/\/[^\s<>()]+)/g;
  let last = 0;
  let match: RegExpExecArray | null;
  let index = 0;

  while ((match = pattern.exec(text)) !== null) {
    if (match.index > last) nodes.push(text.slice(last, match.index));
    const token = match[0];
    if (token.startsWith("http")) {
      nodes.push(
        <a
          key={`${keyPrefix}-l${index++}`}
          href={token}
          target="_blank"
          rel="noreferrer"
          className="underline decoration-dotted underline-offset-2 break-all"
          style={{ color: "var(--amber)" }}
        >
          {token.replace(/^https?:\/\//, "")}
        </a>,
      );
    } else if (token.startsWith("_")) {
      nodes.push(
        <em key={`${keyPrefix}-i${index++}`} className="text-lo not-italic text-[0.85em]">
          {token.slice(1, -1)}
        </em>,
      );
    } else if (token.startsWith("`")) {
      nodes.push(
        <code
          key={`${keyPrefix}-c${index++}`}
          className="num text-[0.82em] px-1 py-0.5 rounded"
          style={{ background: "var(--ink-800)", border: "1px solid var(--ink-700)" }}
        >
          {token.slice(1, -1)}
        </code>,
      );
    } else {
      nodes.push(
        <strong key={`${keyPrefix}-b${index++}`} className="font-semibold text-hi">
          {token.slice(2, -2)}
        </strong>,
      );
    }
    last = match.index + token.length;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

function Markdown({ text }: { text: string }) {
  const blocks = text.split(/\n{2,}/).filter((block) => block.trim());
  return (
    <>
      {blocks.map((block, blockIndex) => {
        const lines = block.split("\n");
        const isList = lines.every((line) => /^\s*[-*]\s+/.test(line));
        if (isList) {
          return (
            <ul key={blockIndex} className="list-disc pl-4 space-y-1">
              {lines.map((line, lineIndex) => (
                <li key={lineIndex} className="text-mid">
                  {inline(line.replace(/^\s*[-*]\s+/, ""), `${blockIndex}-${lineIndex}`)}
                </li>
              ))}
            </ul>
          );
        }
        return (
          <p key={blockIndex} className="text-mid">
            {inline(block, String(blockIndex))}
          </p>
        );
      })}
    </>
  );
}

/* The receipt. Steps stay after the answer lands so the reader can see which
   files it was actually built from, rather than trusting a summary. */
function StepTrail({ steps, active }: { steps: Step[]; active: boolean }) {
  return (
    <ol className="space-y-1.5 pl-0.5" style={{ borderLeft: "1px solid var(--ink-700)" }}>
      {steps.map((step, index) => {
        const isLast = index === steps.length - 1;
        const spinning = active && isLast && (step.kind === "status" || !step.done);
        return (
          <li key={index} className="flex items-start gap-2 pl-3 text-xs" style={{ color: "var(--text-mid)" }}>
            <span
              className={spinning ? "spinner" : ""}
              style={
                spinning
                  ? undefined
                  : {
                      width: 6,
                      height: 6,
                      borderRadius: 999,
                      marginTop: 6,
                      flex: "none",
                      background: step.kind === "tool" && step.done ? "var(--verified)" : "var(--ink-600)",
                    }
              }
              aria-hidden
            />
            <span className="min-w-0">
              {step.kind === "status" ? (
                step.text
              ) : (
                <>
                  {step.label}
                  {step.detail && <span className="text-lo"> · {step.detail}</span>}
                  {step.summary && (
                    <span className="block text-lo num text-[11px] truncate">{step.summary}</span>
                  )}
                </>
              )}
            </span>
          </li>
        );
      })}
    </ol>
  );
}
