import "./globals.css";
import type { ReactNode } from "react";
import { AuthGate } from "@/components/AuthGate";
import { CommandPalette } from "@/components/CommandPalette";
import { HeaderBar } from "@/components/HeaderBar";
import { ToastProvider } from "@/components/Toast";

export const metadata = {
  title: "WhipGuard",
  description: "AI bug council: detects, fixes, and verifies UI bugs end-to-end.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-bg text-hi">
        <ToastProvider>
          <AuthGate>
            <HeaderBar />
            <CommandPalette />
            <main className="max-w-6xl mx-auto px-4 py-7">{children}</main>
          </AuthGate>
        </ToastProvider>
      </body>
    </html>
  );
}
