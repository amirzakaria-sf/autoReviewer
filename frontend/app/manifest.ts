import type { MetadataRoute } from "next";

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "WhipGuard — Autonomous Bug Council",
    short_name: "WhipGuard",
    description: "Detects, scores, fixes and verifies defects in your repositories.",
    // Not "/": the marketing page is the only route a signed-in user never
    // wants, and an installed app that opens on it costs a redirect every
    // single launch.
    start_url: "/dashboard",
    scope: "/",
    // iOS exposes PushManager only to a standalone home-screen app. This
    // line is load-bearing for notifications, not a presentation choice.
    display: "standalone",
    orientation: "portrait-primary",
    background_color: "#08090a",
    theme_color: "#08090a",
    categories: ["developer", "productivity"],
    icons: [
      { src: "/icon-192.png", sizes: "192x192", type: "image/png", purpose: "any" },
      { src: "/icon-512.png", sizes: "512x512", type: "image/png", purpose: "any" },
      // Android crops to whatever shape the launcher uses; the maskable
      // variant keeps the shield inside the safe zone so it is not clipped.
      { src: "/icon-maskable-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
    ],
    shortcuts: [
      { name: "Live activity", url: "/activity" },
      { name: "Repositories", url: "/repos" },
    ],
  };
}
