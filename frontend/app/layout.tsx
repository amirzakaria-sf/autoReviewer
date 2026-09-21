import "./globals.css";
import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";
import { AuthGate } from "@/components/AuthGate";
import { CommandPalette } from "@/components/CommandPalette";
import { CounselSidebar } from "@/components/CounselSidebar";
import { HeaderBar } from "@/components/HeaderBar";
import { MobileNav } from "@/components/MobileNav";
import { ServiceWorkerRegistration } from "@/components/ServiceWorkerRegistration";
import { ToastProvider } from "@/components/Toast";

export const metadata: Metadata = {
  title: "WhipGuard",
  description: "AI bug council: detects, fixes, and verifies UI bugs end-to-end.",
  manifest: "/manifest.webmanifest",
  applicationName: "WhipGuard",
  appleWebApp: {
    // iOS only exposes PushManager to a home-screen web app, never to a
    // Safari tab -- `capable` is part of what makes that install real.
    capable: true,
    title: "WhipGuard",
    statusBarStyle: "black-translucent",
  },
  icons: {
    icon: [{ url: "/icon-192.png", sizes: "192x192", type: "image/png" }],
    apple: [{ url: "/apple-touch-icon.png", sizes: "180x180", type: "image/png" }],
  },
  formatDetection: { telephone: false },
};

/* There was no viewport meta at all, which is the single most consequential
   mobile bug a site can have: without it a phone browser lays the page out at
   ~980px and then scales the whole thing down, so every carefully-sized
   control arrives at roughly a third of its intended size and every media
   query behaves as though the screen were a small desktop.

   `viewportFit: "cover"` lets the page paint under the notch and the home
   indicator; `globals.css` then pays that back with real safe-area padding,
   which is the half people forget. */
export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  themeColor: "#08090a",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-bg text-hi">
        <ToastProvider>
          <AuthGate>
            <ServiceWorkerRegistration />
            <HeaderBar />
            <CommandPalette />
            <CounselSidebar />
            {/* pb-24 on small screens clears the bottom tab bar; the safe-area
                inset below it clears the home indicator on top of that. */}
            <main className="max-w-6xl mx-auto px-4 py-5 pb-36 sm:py-7 sm:pb-7">{children}</main>
            <MobileNav />
          </AuthGate>
        </ToastProvider>
      </body>
    </html>
  );
}
