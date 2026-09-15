import type { Config } from "tailwindcss";

export default {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: "#08090d",
        panel: "#111319",
        panel2: "#161923",
        border: "#232734",
        border2: "#2c3140",
        accent: {
          DEFAULT: "#5b7cfa",
          soft: "#8ea2ff",
          dim: "#1c2340",
        },
      },
      fontFamily: {
        sans: [
          "-apple-system", "BlinkMacSystemFont", "Inter", "Segoe UI", "Roboto", "sans-serif",
        ],
      },
      boxShadow: {
        card: "0 1px 2px rgba(0,0,0,0.4), 0 0 0 1px rgba(255,255,255,0.03)",
        glow: "0 0 0 1px rgba(91,124,250,0.4), 0 0 24px rgba(91,124,250,0.15)",
      },
      backgroundImage: {
        "grid-fade": "radial-gradient(ellipse at top, rgba(91,124,250,0.08), transparent 60%)",
      },
      keyframes: {
        "fade-in": { from: { opacity: "0", transform: "translateY(4px)" }, to: { opacity: "1", transform: "translateY(0)" } },
        "pulse-dot": { "0%, 100%": { opacity: "1" }, "50%": { opacity: "0.35" } },
      },
      animation: {
        "fade-in": "fade-in 0.25s ease-out",
        "pulse-dot": "pulse-dot 1.6s ease-in-out infinite",
      },
    },
  },
  plugins: [],
} satisfies Config;
