"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import {
  disablePush,
  enablePush,
  getExistingSubscription,
  isPushSupported,
  isStandalone,
  permissionState,
} from "@/lib/push";
import { useToast } from "./Toast";

/* The whole point of this control is that nobody should have to go hunting
   through browser settings. Tapping "Enable" IS the permission prompt — the
   request is the first await inside the click handler, because user
   activation does not survive an await on anything else, and a prompt that
   never appears is indistinguishable from one that was denied.
   
   The states are kept apart deliberately. "Blocked" is not "off": a blocked
   site cannot re-prompt itself from script on any browser, so the only honest
   thing to do is say so and explain where the switch actually lives. */

type Status = { configured: boolean; public_key: string; devices: number };

export function NotificationSettings() {
  const toast = useToast();
  const [status, setStatus] = useState<Status | null>(null);
  const [subscribed, setSubscribed] = useState<boolean | null>(null);
  const [permission, setPermission] = useState<NotificationPermission | "unsupported">("unsupported");
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    setPermission(permissionState());
    try {
      setStatus(await api.pushStatus());
    } catch {
      setStatus(null);
    }
    try {
      setSubscribed(Boolean(await getExistingSubscription()));
    } catch {
      setSubscribed(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const supported = isPushSupported();
  const standalone = isStandalone();
  const blocked = permission === "denied";
  const on = subscribed === true && permission === "granted";

  async function toggle() {
    setBusy(true);
    try {
      if (on) {
        await disablePush();
        toast("info", "Notifications turned off for this device");
      } else {
        // The key comes from the API, so it cannot be stale relative to the
        // deployment the way a build-time inlined one can.
        const current = status ?? (await api.pushStatus());
        await enablePush(current.public_key);
        toast("success", "Notifications on — this device is registered");
      }
      await refresh();
    } catch (error) {
      toast("error", error instanceof Error ? error.message : "Could not change notifications");
    } finally {
      setBusy(false);
    }
  }

  async function sendTest() {
    setBusy(true);
    try {
      const result = await api.pushTest();
      toast(
        result.delivered > 0 ? "success" : "error",
        result.delivered > 0
          ? `Sent to ${result.delivered} device${result.delivered === 1 ? "" : "s"}`
          : "Nothing was delivered — the subscription may have expired. Turn it off and on again.",
      );
    } catch {
      toast("error", "Could not send the test notification");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card p-5">
      <h2 className="font-semibold mb-1">Notifications</h2>
      <p className="text-sm text-mid mb-4 leading-relaxed">
        A push when a bug is raised, when a fix is ready for your review, and when a verification fails.
        Per device — turning this on here does not turn it on for your phone.
      </p>

      {status?.configured === false ? (
        <p className="text-sm text-lo">Push is not configured on this deployment.</p>
      ) : !supported ? (
        <div className="text-sm text-mid leading-relaxed">
          {/* iOS exposes PushManager only to a home-screen web app. Saying
              "unsupported" to someone holding a supported phone is the least
              useful true statement available, so name the actual step. */}
          {standalone ? (
            "This browser does not support push notifications."
          ) : (
            <>
              <span className="text-hi">Add WhipGuard to your home screen first.</span> On iPhone, tap Share
              then <span className="text-hi">Add to Home Screen</span>, and open it from there — iOS only
              allows notifications for installed apps.
            </>
          )}
        </div>
      ) : (
        <div className="space-y-3">
          <div className="flex items-center justify-between gap-4 flex-wrap">
            <div className="min-w-0">
              <div className="text-sm">
                {on ? "On for this device" : blocked ? "Blocked by your browser" : "Off for this device"}
              </div>
              {status && status.devices > 0 && (
                <div className="text-xs text-lo mt-0.5 num">
                  {status.devices} device{status.devices === 1 ? "" : "s"} registered on your account
                </div>
              )}
            </div>

            <button
              type="button"
              role="switch"
              aria-checked={on}
              aria-label="Enable notifications"
              disabled={busy || blocked}
              onClick={toggle}
              className="relative shrink-0 rounded-full transition disabled:opacity-40 disabled:cursor-not-allowed"
              style={{
                width: 52,
                height: 30,
                background: on ? "var(--verified)" : "var(--ink-600)",
                border: `1px solid ${on ? "var(--verified-dim)" : "var(--ink-500)"}`,
              }}
            >
              <span
                className="absolute rounded-full transition-all"
                style={{
                  width: 22,
                  height: 22,
                  top: 3,
                  left: on ? 26 : 3,
                  background: on ? "#06281c" : "var(--text-mid)",
                }}
              />
            </button>
          </div>

          {blocked && (
            <p className="text-xs text-mid leading-relaxed">
              A blocked site cannot ask again from the page — no browser allows that. Open the padlock (or
              the <span className="text-hi">aA</span> menu on iOS) next to the address bar, allow
              notifications for this site, then reload.
            </p>
          )}

          {on && (
            <button type="button" onClick={sendTest} disabled={busy} className="btn btn-ghost text-sm">
              Send a test notification
            </button>
          )}

          {on && (
            <p className="text-xs text-lo leading-relaxed">
              A test proves the whole path — the key, the service worker, and that this device is registered
              to your account rather than to whoever last signed in on it.
            </p>
          )}
        </div>
      )}
    </section>
  );
}
