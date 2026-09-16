import type { Config } from "tailwindcss";

/* Token names map onto the CSS custom properties in app/globals.css rather
   than repeating their hex values, so the palette has exactly one definition
   and a theme change cannot half-apply. */
export default {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: "var(--ink-900)",
        panel: "var(--ink-800)",
        panel2: "var(--ink-750)",
        raised: "var(--ink-700)",
        border: "var(--ink-700)",
        border2: "var(--ink-600)",
        hi: "var(--text-hi)",
        mid: "var(--text-mid)",
        lo: "var(--text-lo)",
        accent: {
          DEFAULT: "var(--amber)",
          soft: "#ffcd68",
          dim: "var(--amber-dim)",
        },
        verified: "var(--verified)",
        failed: "var(--failed)",
        pending: "var(--pending)",
      },
      fontFamily: {
        sans: ["-apple-system", "BlinkMacSystemFont", "Inter", "Segoe UI", "Roboto", "sans-serif"],
        mono: ["ui-monospace", "SF Mono", "JetBrains Mono", "Menlo", "monospace"],
      },
      boxShadow: {
        card: "inset 0 1px 0 rgba(255,255,255,0.035), 0 1px 2px rgba(0,0,0,0.5), 0 8px 24px -12px rgba(0,0,0,0.7)",
        glow: "0 0 0 1px rgba(255,178,36,0.35), 0 0 28px rgba(255,178,36,0.12)",
      },
      keyframes: {
        "fade-in": { from: { opacity: "0", transform: "translateY(4px)" }, to: { opacity: "1", transform: "translateY(0)" } },
        "pulse-dot": { "0%, 100%": { opacity: "1" }, "50%": { opacity: "0.3" } },
      },
      animation: {
        "fade-in": "fade-in 0.28s ease both",
        "pulse-dot": "pulse-dot 1.8s ease-in-out infinite",
      },
    },
  },
  plugins: [],
} satisfies Config;
