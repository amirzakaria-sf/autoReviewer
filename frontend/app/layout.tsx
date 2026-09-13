import "./globals.css";
import type { ReactNode } from "react";

export const metadata = {
  title: "WhipGuard",
  description: "AI bug council: detects, fixes, and verifies UI bugs end-to-end.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-bg text-white">
        <div className="border-b border-border bg-panel/60 backdrop-blur sticky top-0 z-10">
          <div className="max-w-6xl mx-auto px-4 py-3 flex items-center justify-between">
            <a href="/" className="flex items-center gap-2 font-semibold tracking-tight">
              <span className="text-lg">🛡️</span> WhipGuard
            </a>
            <div className="flex items-center gap-4 text-sm text-gray-400">
              <a href="/" className="hover:text-white">Overview</a>
              <a href="/activity" className="hover:text-white">Live activity</a>
              <span className="badge badge-green">detection: on</span>
            </div>
          </div>
        </div>
        <main className="max-w-6xl mx-auto px-4 py-6">{children}</main>
      </body>
    </html>
  );
}
