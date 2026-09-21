import { api } from "./api";

/* Browser-side Web Push.
 *
 * The one thing worth understanding before changing anything here: a push
 * subscription belongs to the ORIGIN and the service worker registration, not
 * to a login session. It survives logout, account switches and app restarts.
 * The server records who owns it once, at subscribe time — so a device that
 * subscribed as one person keeps delivering to that person forever, while the
 * settings toggle reports "enabled" because all it can see is browser state.
 *
 * `syncPushSubscription` is the answer: re-POST the existing subscription on
 * every authenticated session. The server upserts on the endpoint, so that
 * transfers ownership and self-heals an already-wrong device with no
 * migration and nothing for anyone to click.
 */

function urlBase64ToUint8Array(base64: string): Uint8Array {
  const padding = "=".repeat((4 - (base64.length % 4)) % 4);
  const normalised = (base64 + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = window.atob(normalised);
  const out = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i += 1) out[i] = raw.charCodeAt(i);
  return out;
}

/** iOS exposes PushManager ONLY to a home-screen web app, never to a Safari
 *  tab — so this doubles as the "have they installed it yet" check. */
export function isPushSupported(): boolean {
  return (
    typeof window !== "undefined" &&
    "serviceWorker" in navigator &&
    "PushManager" in window &&
    "Notification" in window
  );
}

/** True when the page is running as an installed app rather than a tab.
 *  On iOS this is the difference between push working and push not existing. */
export function isStandalone(): boolean {
  if (typeof window === "undefined") return false;
  return (
    window.matchMedia?.("(display-mode: standalone)").matches ||
    // Safari's own non-standard flag, which is the only signal on iOS.
    (window.navigator as unknown as { standalone?: boolean }).standalone === true
  );
}

export function permissionState(): NotificationPermission | "unsupported" {
  if (!isPushSupported()) return "unsupported";
  return Notification.permission;
}

export async function getExistingSubscription(): Promise<PushSubscription | null> {
  if (!isPushSupported()) return null;
  const registration = await navigator.serviceWorker.ready;
  return registration.pushManager.getSubscription();
}

/**
 * Ask for permission and register this device.
 *
 * MUST be called directly from a click handler. `Notification.requestPermission()`
 * needs user activation, and activation does not survive an `await` on anything
 * that isn't part of the same task — so the permission request is the FIRST
 * await here, before the network call that fetches the key.
 */
export async function enablePush(vapidPublicKey: string): Promise<void> {
  if (!isPushSupported()) {
    throw new Error(
      isStandalone()
        ? "This browser does not support push notifications."
        : "Add WhipGuard to your home screen first — iOS only allows notifications for installed apps.",
    );
  }
  if (!vapidPublicKey) {
    throw new Error("Push notifications are not configured on this deployment.");
  }

  const permission = await Notification.requestPermission();
  if (permission !== "granted") {
    throw new Error(
      permission === "denied"
        ? "Notifications are blocked for this site. Allow them in your browser settings, then try again."
        : "Notification permission was dismissed.",
    );
  }

  const registration = await navigator.serviceWorker.ready;
  const existing = await registration.pushManager.getSubscription();
  const subscription =
    existing ||
    (await registration.pushManager.subscribe({
      // Non-negotiable: iOS revokes the subscription if a push ever fails to
      // produce a visible notification, which is the contract this opts into.
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToUint8Array(vapidPublicKey),
    }));

  await api.pushSubscribe(subscription.toJSON());
}

/**
 * Turn it off for this device.
 *
 * Unsubscribes in the browser as well as dropping the server row, because
 * this is a deliberate "off" — unlike logout, where the browser subscription
 * is deliberately kept so the next login can re-bind without a fresh
 * permission grant that iOS will not reliably re-prompt for.
 */
export async function disablePush(): Promise<void> {
  const subscription = await getExistingSubscription();
  if (!subscription) return;
  const { endpoint } = subscription;
  await subscription.unsubscribe().catch(() => {});
  await api.pushUnsubscribe(endpoint).catch(() => {});
}

/**
 * Re-bind this browser's existing subscription to the CURRENT account.
 *
 * Called once per authenticated session, from the auth gate — deliberately not
 * from the settings page, which is rarely visited and is exactly why this
 * class of bug goes unnoticed for weeks. Never requests permission (that needs
 * a user gesture and this runs on load) and never throws.
 */
export async function syncPushSubscription(): Promise<boolean> {
  try {
    if (!isPushSupported() || Notification.permission !== "granted") return false;
    const subscription = await getExistingSubscription();
    if (!subscription) return false;
    await api.pushSubscribe(subscription.toJSON());
    return true;
  } catch {
    // Best-effort. A failed re-bind must never break app startup; the next
    // session tries again.
    return false;
  }
}

/**
 * Drop only the SERVER row, on logout.
 *
 * Deliberately does NOT call `subscription.unsubscribe()`: that destroys the
 * browser subscription and forces a fresh permission grant, which iOS will not
 * reliably re-prompt for once dismissed. Keeping it means the next login
 * re-binds instantly through `syncPushSubscription`.
 */
export async function releasePushBinding(): Promise<void> {
  try {
    const subscription = await getExistingSubscription();
    if (subscription) await api.pushUnsubscribe(subscription.endpoint);
  } catch {
    // Must never block logout.
  }
}
