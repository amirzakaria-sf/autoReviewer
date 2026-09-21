/* WhipGuard service worker. Hand-rolled -- no Workbox, no build-time precache
   manifest, so it never needs to know Next's per-build hashed chunk names.

   Scope is deliberately narrow. This is a live, authenticated dashboard with
   no local data store, so there are only three jobs here:
     1. never cache API or auth responses,
     2. make the static shell load instantly on a repeat visit,
     3. show something other than the browser's error page when offline.

   The push half is the part with teeth -- see the comment above the push
   handler. */

const CACHE_VERSION = "whipguard-shell-v2";

self.addEventListener("install", () => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== CACHE_VERSION).map((key) => caches.delete(key))))
      .then(() => self.clients.claim()),
  );
});

function isStaticAsset(url) {
  return (
    url.pathname.startsWith("/_next/static/") ||
    url.pathname === "/manifest.webmanifest" ||
    /^\/(icon|icon-192|icon-512|icon-maskable-512|apple-touch-icon)\.png$/.test(url.pathname)
  );
}

const OFFLINE_PAGE = `<!DOCTYPE html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Offline — WhipGuard</title></head>
<body style="margin:0;height:100vh;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:10px;
background:#08090a;color:#f2f0ed;font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;text-align:center;padding:24px">
<svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="#ffb224" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3.2 19 6v5.6c0 4-2.9 7.6-7 8.5-4.1-.9-7-4.5-7-8.5V6z"/></svg>
<div style="font-weight:600">You're offline</div>
<div style="color:#a9a6a1;font-size:14px;max-width:28ch;line-height:1.5">
WhipGuard reads live data from your repositories, so it needs a connection. It will reconnect on its own.</div>
</body></html>`;

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  // Live authenticated data always hits the network. A cached /api/issues is
  // worse than no issues: it is a stale verdict presented as a current one.
  // WebSocket upgrades never reach a fetch handler, so there is nothing to
  // special-case for the activity feed.
  if (url.pathname.startsWith("/api/")) return;

  if (isStaticAsset(url)) {
    event.respondWith(
      caches.open(CACHE_VERSION).then(async (cache) => {
        const cached = await cache.match(request);
        const network = fetch(request)
          .then((response) => {
            if (response.ok) cache.put(request, response.clone());
            return response;
          })
          .catch(() => cached);
        return cached || network;
      }),
    );
    return;
  }

  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request)
        .then((response) => {
          const copy = response.clone();
          caches.open(CACHE_VERSION).then((cache) => cache.put(request, copy));
          return response;
        })
        .catch(async () => {
          const cache = await caches.open(CACHE_VERSION);
          const cached = await cache.match(request);
          return cached || new Response(OFFLINE_PAGE, { headers: { "Content-Type": "text/html" } });
        }),
    );
  }
});

/* iOS/WebKit REVOKES a push subscription if a push event does not produce a
   user-visible notification -- that is the `userVisibleOnly: true` contract we
   subscribe with. There is no server-side signal when it happens: Apple keeps
   returning 201 for the dead token until it expires, so the backend goes on
   believing delivery succeeds.

   A sibling project lost push on a device for six days to exactly this, via an
   early `return` on a payload-less push and a silent `catch` on a malformed
   one. So every path below ends in showNotification, and there is no bare
   return anywhere in the handler. */
function parsePushPayload(event) {
  const fallback = { title: "WhipGuard", body: "You have a new update.", url: "/dashboard" };
  if (!event.data) return fallback;

  try {
    const data = event.data.json();
    return {
      title: data.title || fallback.title,
      body: data.body || fallback.body,
      url: data.url || fallback.url,
      icon: data.icon,
      tag: data.tag,
    };
  } catch (_err) {
    try {
      const text = event.data.text();
      return { ...fallback, body: text || fallback.body };
    } catch (_innerErr) {
      return fallback;
    }
  }
}

self.addEventListener("push", (event) => {
  const data = parsePushPayload(event);

  event.waitUntil(
    self.registration.showNotification(data.title, {
      body: data.body,
      icon: data.icon || "/icon-192.png",
      badge: "/icon.png",
      // `tag` collapses repeats of the same condition into one notification
      // instead of stacking five of them for one flapping fix.
      tag: data.tag,
      data: { url: data.url },
      vibrate: [100, 50, 100],
    }),
  );
});

/* Push services rotate endpoints -- Apple and Chrome both do. Without this the
   device silently stops receiving: the old endpoint dies and nothing tells the
   server about the replacement. */
self.addEventListener("pushsubscriptionchange", (event) => {
  event.waitUntil(
    (async () => {
      try {
        const applicationServerKey =
          event.oldSubscription?.options?.applicationServerKey ||
          event.newSubscription?.options?.applicationServerKey;
        if (!applicationServerKey) return;

        const subscription =
          event.newSubscription ||
          (await self.registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey }));

        await fetch("/api/push/subscribe", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "include",
          body: JSON.stringify(subscription.toJSON()),
        });
      } catch (_err) {
        // Nothing useful to do from here. The next authenticated app open
        // re-binds through syncPushSubscription().
      }
    })(),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = new URL(event.notification.data?.url || "/dashboard", self.location.origin).href;

  event.waitUntil(
    clients.matchAll({ type: "window", includeUncontrolled: true }).then((windowClients) => {
      // Focus an existing tab on the same page rather than opening a second
      // copy of the app, which on a phone means two entries in the switcher.
      for (const client of windowClients) {
        if (client.url === target && "focus" in client) return client.focus();
      }
      for (const client of windowClients) {
        if ("focus" in client && "navigate" in client) {
          return client.focus().then(() => client.navigate(target));
        }
      }
      if (clients.openWindow) return clients.openWindow(target);
    }),
  );
});
