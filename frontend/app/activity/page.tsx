"use client";

import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";

type Event = { type: string; message: string; at: string };

export default function ActivityPage() {
  const [events, setEvents] = useState<Event[]>([]);
  const [connected, setConnected] = useState(false);
  const socketRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    const ws = new WebSocket(api.wsUrl());
    socketRef.current = ws;
    ws.onopen = () => setConnected(true);
    ws.onclose = () => setConnected(false);
    ws.onmessage = (msg) => {
      const data = JSON.parse(msg.data);
      setEvents((prev) => [{ ...data, at: new Date().toLocaleTimeString() }, ...prev].slice(0, 200));
    };
    return () => ws.close();
  }, []);

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <h1 className="text-xl font-semibold">Live activity</h1>
        <span className={`badge ${connected ? "badge-green" : "badge-gray"}`}>
          {connected ? "connected" : "disconnected"}
        </span>
      </div>
      <p className="text-sm text-gray-500">
        Streams council node transitions as they happen: detection, jury verdicts, score computed,
        deploy in progress, oracle re-check, outcome-check result.
      </p>
      <div className="border border-border rounded-lg divide-y divide-border">
        {events.map((e, i) => (
          <div key={i} className="px-3 py-2 text-sm flex items-center justify-between">
            <span>{e.message}</span>
            <span className="text-gray-500 text-xs">{e.at}</span>
          </div>
        ))}
        {events.length === 0 && (
          <div className="px-3 py-6 text-center text-gray-500 text-sm">
            No events yet — trigger a run to see it stream here live.
          </div>
        )}
      </div>
    </div>
  );
}
