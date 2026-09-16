"use client";

/* Action feedback. Approving or rejecting a fix previously produced no
   acknowledgement at all beyond a row quietly changing state some seconds
   later, which reads as "did my click register?" -- the single most common
   way a dashboard feels broken while working correctly.

   Deliberately not a library: three states, one stack, no positioning
   engine. */

import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";

type Tone = "success" | "error" | "info";
type Toast = { id: number; tone: Tone; message: string };

const ToastContext = createContext<(tone: Tone, message: string) => void>(() => {});

export function useToast() {
  return useContext(ToastContext);
}

const TONE_STYLES: Record<Tone, { border: string; dot: string }> = {
  success: { border: "var(--verified-dim)", dot: "var(--verified)" },
  error: { border: "var(--failed-dim)", dot: "var(--failed)" },
  info: { border: "var(--ink-600)", dot: "var(--amber)" },
};

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const push = useCallback((tone: Tone, message: string) => {
    const id = Date.now() + Math.random();
    setToasts((current) => [...current, { id, tone, message }]);
    setTimeout(() => setToasts((current) => current.filter((toast) => toast.id !== id)), 4200);
  }, []);

  const value = useMemo(() => push, [push]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="fixed bottom-5 right-5 z-50 flex flex-col gap-2 pointer-events-none" role="status" aria-live="polite">
        {toasts.map((toast) => (
          <div
            key={toast.id}
            className="card animate-fade-in pointer-events-auto flex items-center gap-2.5 px-3.5 py-2.5 text-sm max-w-sm"
            style={{ borderColor: TONE_STYLES[toast.tone].border }}
          >
            <span className="dot" style={{ background: TONE_STYLES[toast.tone].dot }} />
            <span className="text-hi">{toast.message}</span>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}
