"use client";

/* The live feed.
 *
 * Two things this page has to get right that the old version did not.
 *
 * It must survive a dropped socket. A redeploy closes every connection, and
 * a feed that goes quiet forever afterwards looks identical to a system with
 * nothing to report — so it reconnects with backoff and says which state it
 * is in.
 *
 * And it must be readable at speed. Council runs emit bursts, so events are
 * typed and colour-coded by what they mean rather than rendered as a flat
 * list of sentences, and the stream can be paused — you cannot read a log
 * that is moving.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { api, refreshSession } from "@/lib/api";
import { EmptyState } from "@/components/EmptyState";

type FeedEvent = {
  id: number;
  type: string;
  node?: string;
  kind?: string;
  status?: string;
  message: string;
  at: string;
};

const MAX_EVENTS = 300;
const FILTERS = [
  { key: "", label: "Everything" },
  { key: "run", label: "Runs" },
  { key: "node", label: "Council steps" },
  { key: "counsel_job", label: "Counsel" },
];

/* Colour carries meaning here, so it is derived from the event's own status
   rather than from its type: a failed node and a failed run should read the
   same way at a glance. */
function toneFor(event: FeedEvent): string {
  const text = `${event.status ?? ""} ${event.message}`.toLowerCase();
  if (/fail|error|reject|mismatch|revoked/.test(text)) return "var(--failed)";
  if (/done|pass|verified|complete|merged|approved|agree/.test(text)) return "var(--verified)";
  if (/started|running|queued|working/.test(text)) return "var(--amber)";
  return "var(--ink-600)";
}

export default function ActivityPage() {
  const [events, setEvents] = useState<FeedEvent[]>([]);
  const [status, setStatus] = useState<"connecting" | "live" | "reconnecting">("connecting");
  const [filter, setFilter] = useState("");
  const [paused, setPaused] = useState(false);

  const pausedRef = useRef(paused);
  pausedRef.current = paused;
  const bufferRef = useRef<FeedEvent[]>([]);
  const nextId = useRef(0);
  const socketRef = useRef<WebSocket | null>(null);
  const retryRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const attemptRef = useRef(0);

  const connect = useCallback(() => {
    const socket = new WebSocket(api.wsUrl());
    socketRef.current = socket;

    socket.onopen = () => {
      attemptRef.current = 0;
      setStatus("live");
    };
    socket.onmessage = (message) => {
      try {
        const data = JSON.parse(message.data);
        const event: FeedEvent = {
          id: nextId.current++,
          type: data.type ?? "event",
          node: data.node,
          kind: data.kind,
          status: data.status,
          message: data.message ?? JSON.stringify(data),
          at: new Date().toLocaleTimeString(),
        };
        // While paused, events accumulate off-screen rather than being
        // dropped — otherwise pausing to read loses everything that happened
        // while you were reading.
        if (pausedRef.current) {
          bufferRef.current = [event, ...bufferRef.current].slice(0, MAX_EVENTS);
          return;
        }
        setEvents((current) => [event, ...current].slice(0, MAX_EVENTS));
      } catch {
        /* a frame we don't understand is not worth breaking the feed for */
      }
    };
    socket.onclose = async (event) => {
      setStatus("reconnecting");
      // 4401 means the socket's own auth check rejected the cookie, which an
      // expired access token does on every page left open for an hour.
      // Reconnecting without refreshing first just gets rejected again, so
      // the feed would sit in "reconnecting" until a reload.
      if (event.code === 4401) {
        const refreshed = await refreshSession();
        if (refreshed) {
          attemptRef.current = 0;
          retryRef.current = setTimeout(connect, 0);
          return;
        }
      }
      // Backoff, capped: a redeploy takes seconds, but a backend that stays
      // down should not be hammered once a second forever.
      const delay = Math.min(1000 * 2 ** attemptRef.current, 15000);
      attemptRef.current += 1;
      retryRef.current = setTimeout(connect, delay);
    };
  }, []);

  useEffect(() => {
    connect();
    return () => {
      if (retryRef.current) clearTimeout(retryRef.current);
      socketRef.current?.close();
    };
  }, [connect]);

  function resume() {
    setEvents((current) => [...bufferRef.current, ...current].slice(0, MAX_EVENTS));
    bufferRef.current = [];
    setPaused(false);
  }

  const visible = filter ? events.filter((event) => event.type === filter) : events;

  return (
    <div className="space-y-5 animate-fade-in">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-lg font-semibold flex items-center gap-2.5">
            Live activity
            <span className={`badge ${status === "live" ? "badge-green" : "badge-amber"}`}>
              <span
                className={`dot ${status === "live" ? "" : "animate-pulse-dot"}`}
                style={{ background: status === "live" ? "var(--verified)" : "var(--amber)" }}
              />
              {status}
            </span>
          </h1>
          <p className="text-xs text-lo mt-0.5">
            Council node transitions as they happen — detection, jury verdicts, deploys, outcome checks.
          </p>
        </div>

        <div className="flex items-center gap-2">
          {paused && bufferRef.current.length > 0 && (
            <span className="badge badge-amber num">{bufferRef.current.length} waiting</span>
          )}
          <button
            onClick={() => (paused ? resume() : setPaused(true))}
            className="btn btn-ghost px-3 py-1.5"
          >
            {paused ? "Resume" : "Pause"}
          </button>
        </div>
      </div>

      <div className="flex gap-1 text-xs flex-wrap">
        {FILTERS.map((option) => (
          <button
            key={option.key}
            onClick={() => setFilter(option.key)}
            className="px-2.5 py-1 rounded-md border transition"
            style={
              filter === option.key
                ? { borderColor: "var(--amber-dim)", background: "rgba(255,178,36,0.1)", color: "var(--amber)" }
                : { borderColor: "var(--ink-700)", color: "var(--text-lo)" }
            }
          >
            {option.label}
          </button>
        ))}
      </div>

      {visible.length === 0 ? (
        <EmptyState
          title={status === "live" ? "Nothing yet" : "Waiting for the connection"}
          hint={
            status === "live"
              ? "Trigger a scan from the overview and the council's steps will stream here as they run."
              : "The backend may be restarting. This reconnects on its own."
          }
        />
      ) : (
        <div className="panel overflow-hidden">
          {visible.map((event) => (
            <div key={event.id} className="hairline first:border-t-0 px-3.5 py-2.5 flex items-start gap-3">
              <span
                className="dot shrink-0"
                style={{ background: toneFor(event), marginTop: 7 }}
                aria-hidden
              />
              <div className="min-w-0 flex-1">
                <p className="text-sm leading-snug">{event.message}</p>
                {(event.node || event.kind) && (
                  <span className="text-[11px] text-lo num">{event.node ?? event.kind}</span>
                )}
              </div>
              <span className="text-[11px] text-lo num shrink-0">{event.at}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
