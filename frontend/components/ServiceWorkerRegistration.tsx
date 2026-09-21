"use client";

import { useEffect } from "react";

/* Registers the service worker, and nothing else.
   `updateViaCache: "none"` so the browser re-fetches sw.js on every check
   rather than serving a cached copy for up to 24 hours -- otherwise a deploy
   that fixes the worker can take a day to actually reach a device.
   A failure here is non-fatal by design: the app is fully functional without
   a service worker, it just loses offline shell and push. */
export function ServiceWorkerRegistration() {
  useEffect(() => {
    if (!("serviceWorker" in navigator)) return;
    navigator.serviceWorker.register("/sw.js", { scope: "/", updateViaCache: "none" }).catch(() => {});
  }, []);

  return null;
}
