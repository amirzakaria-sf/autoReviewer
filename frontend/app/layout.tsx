import "./globals.css";
import type { ReactNode } from "react";
import { AuthGate } from "@/components/AuthGate";
import { HeaderBar } from "@/components/HeaderBar";

export const metadata = {
  title: "WhipGuard",
  description: "AI bug council: detects, fixes, and verifies UI bugs end-to-end.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-bg text-white">
        <AuthGate>
          <HeaderBar />
          <main className="max-w-6xl mx-auto px-4 py-6">{children}</main>
        </AuthGate>
      </body>
    </html>
  );
}
