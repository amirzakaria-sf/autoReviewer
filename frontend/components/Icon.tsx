/* One icon set, drawn on a 24×24 grid, stroked in `currentColor`.
 *
 * What was here before was emoji — 🔎 ⚖️ 🙋 🚀 — and emoji make poor UI icons
 * for three reasons that all show at once. They are rendered by the operating
 * system, so the same screen looks different on a Mac, a Pixel and Windows.
 * They are full-colour, which fights a palette built deliberately from warm
 * graphite and one amber accent. And they cannot take `currentColor`, so an
 * icon can never agree with the text beside it or dim with its container.
 *
 * These are thin (1.6 stroke), geometric and monochrome — closer to an
 * instrument's markings than to an illustration, which is what globals.css
 * says this product is meant to feel like. Anything that needs colour gets it
 * from the element around it.
 */

export type IconName =
  | "shield"
  | "search"
  | "scales"
  | "approve"
  | "deploy"
  | "loop"
  | "merge"
  | "monitor"
  | "server"
  | "lock"
  | "bolt"
  | "accessibility"
  | "document"
  | "compass"
  | "check"
  | "halt"
  | "chart"
  | "spark"
  | "alert"
  | "hourglass"
  | "chat"
  | "home"
  | "repos"
  | "activity"
  | "team"
  | "user";

const PATHS: Record<IconName, React.ReactNode> = {
  // The mark. Same silhouette as the app icon, so the header and the home
  // screen agree.
  shield: <path d="M12 3.2 19 6v5.6c0 4-2.9 7.6-7 8.5-4.1-.9-7-4.5-7-8.5V6z" />,

  search: (
    <>
      <circle cx="10.8" cy="10.8" r="6.3" />
      <path d="m15.4 15.4 4.3 4.3" />
    </>
  ),

  // Adversarial jury: a beam with two pans, deliberately level — the point is
  // that both sides are argued, not that one wins.
  scales: (
    <>
      <path d="M12 4.2v15.4M7.5 19.6h9M5 7.4h14M12 4.2 5 7.4M12 4.2l7 3.2" />
      <path d="M2.6 13.2a2.4 2.4 0 0 0 4.8 0L5 7.6z" />
      <path d="M16.6 13.2a2.4 2.4 0 0 0 4.8 0L19 7.6z" />
    </>
  ),

  // Human approval: a person, and a tick that is theirs to give.
  approve: (
    <>
      <circle cx="9.5" cy="8" r="3.3" />
      <path d="M3.5 19.5c0-3.3 2.7-5.4 6-5.4 1 0 1.9.2 2.7.5" />
      <path d="m14.5 17.4 2 2 4-4.4" />
    </>
  ),

  // Deploy: a branch pushed up and out, not a rocket.
  deploy: (
    <>
      <path d="M12 20V8.4" />
      <path d="m7.6 12.8 4.4-4.4 4.4 4.4" />
      <path d="M4.5 4.6h15" />
    </>
  ),

  // Outcome check: read back around the loop.
  loop: (
    <>
      <path d="M4.6 11.4a7.4 7.4 0 0 1 12.7-4.6l2.1 2.1" />
      <path d="M19.4 12.6a7.4 7.4 0 0 1-12.7 4.6l-2.1-2.1" />
      <path d="M19.6 4.6v4.4h-4.4M4.4 19.4V15h4.4" />
    </>
  ),

  merge: (
    <>
      <circle cx="7" cy="5.8" r="2.2" />
      <circle cx="7" cy="18.2" r="2.2" />
      <circle cx="17" cy="12" r="2.2" />
      <path d="M7 8v8M7 10.4c0 3 2.3 4.4 7.8 1.6" />
    </>
  ),

  monitor: (
    <>
      <rect x="3.2" y="4.6" width="17.6" height="11.4" rx="1.8" />
      <path d="M9.4 20h5.2M12 16v4" />
    </>
  ),

  server: (
    <>
      <rect x="7.4" y="7.4" width="9.2" height="9.2" rx="1.4" />
      <path d="M10 2.6v3.4M14 2.6v3.4M10 18v3.4M14 18v3.4M2.6 10H6M2.6 14H6M18 10h3.4M18 14h3.4" />
    </>
  ),

  lock: (
    <>
      <rect x="4.6" y="10.4" width="14.8" height="9.6" rx="2" />
      <path d="M8.2 10.4V7.6a3.8 3.8 0 0 1 7.6 0v2.8" />
    </>
  ),

  bolt: <path d="M13.4 2.6 5 13.6h5.6L10.6 21.4 19 10.4h-5.6z" />,

  accessibility: (
    <>
      <circle cx="12" cy="4.6" r="1.8" />
      <path d="M4.6 8.6c4.8 1.6 9.9 1.6 14.8 0" />
      <path d="M12 8.6v5.2M12 13.8l-3 7.2M12 13.8l3 7.2" />
    </>
  ),

  document: (
    <>
      <path d="M13.4 3H7a1.8 1.8 0 0 0-1.8 1.8v14.4A1.8 1.8 0 0 0 7 21h10a1.8 1.8 0 0 0 1.8-1.8V8.4z" />
      <path d="M13.4 3v5.4h5.4M8.8 13h6.4M8.8 16.6h4.4" />
    </>
  ),

  compass: (
    <>
      <circle cx="12" cy="12" r="8.6" />
      <path d="m15.2 8.8-1.9 4.5-4.5 1.9 1.9-4.5z" />
    </>
  ),

  check: (
    <>
      <circle cx="12" cy="12" r="8.6" />
      <path d="m8.2 12.2 2.6 2.6 5-5.4" />
    </>
  ),

  // Fails closed: a shield that stops, not a red octagon.
  halt: (
    <>
      <path d="M12 3.2 19 6v5.6c0 4-2.9 7.6-7 8.5-4.1-.9-7-4.5-7-8.5V6z" />
      <path d="m9.4 9.4 5.2 5.2M14.6 9.4l-5.2 5.2" />
    </>
  ),

  chart: (
    <>
      <path d="M4 20.4h16.4" />
      <path d="M7 20.4v-6M12 20.4V6.6M17 20.4v-9.2" />
    </>
  ),

  spark: (
    <>
      <path d="M12 3.4 13.7 9l5.6 1.7-5.6 1.7L12 18l-1.7-5.6L4.7 10.7 10.3 9z" />
      <path d="M18.6 3v3M20.1 4.5h-3" />
    </>
  ),

  alert: (
    <>
      <path d="M12 4.2 21 19.6H3z" />
      <path d="M12 10v4.2M12 16.8v.2" />
    </>
  ),

  // The bottom tab bar. Slightly heavier shapes than the rest of the set,
  // because these are read at 20px in peripheral vision rather than beside a
  // heading.
  home: <path d="M3.4 11.6 12 4.2l8.6 7.4M5.6 10v9.8h4.6v-5.6h3.6v5.6h4.6V10" />,

  repos: (
    <>
      <path d="M4.2 5.6A2.4 2.4 0 0 1 6.6 3.2H19v13.4H6.6a2.4 2.4 0 0 0-2.4 2.4z" />
      <path d="M4.2 19a2.4 2.4 0 0 1 2.4-2.4H19v4.2H6.6A2.4 2.4 0 0 1 4.2 19z" />
    </>
  ),

  activity: <path d="M2.8 12h4L9.4 4.6l5.2 14.8 2.4-7.4h4.2" />,

  team: (
    <>
      <circle cx="9" cy="8" r="3.2" />
      <path d="M3 20c0-3.3 2.7-5.5 6-5.5s6 2.2 6 5.5" />
      <path d="M16 11.2a3 3 0 1 0 0-5.9M17.5 14.8c2.1.6 3.5 2.4 3.5 5.2" />
    </>
  ),

  user: (
    <>
      <circle cx="12" cy="8" r="3.4" />
      <path d="M5 20c0-3.6 3.1-6 7-6s7 2.4 7 6" />
    </>
  ),

  // Counsel's launcher. A speech bubble with a seam of lines -- a
  // conversation about a file, which is what Counsel actually is.
  chat: (
    <>
      <path d="M20.4 11.6a7.9 7.9 0 0 1-8.4 7.9 8.6 8.6 0 0 1-3.4-.7L3.6 20.4l1.7-4.8a7.9 7.9 0 0 1 6.7-12 7.9 7.9 0 0 1 8.4 8z" />
      <path d="M8.8 10.2h6.4M8.8 13.4h4" />
    </>
  ),

  hourglass: (
    <>
      <path d="M6.4 3h11.2M6.4 21h11.2" />
      <path d="M7.6 3v3.4c0 2 4.4 3.6 4.4 5.6s-4.4 3.6-4.4 5.6V21" />
      <path d="M16.4 3v3.4c0 2-4.4 3.6-4.4 5.6s4.4 3.6 4.4 5.6V21" />
    </>
  ),
};

export function Icon({
  name,
  size = 20,
  className = "",
  strokeWidth = 1.6,
}: {
  name: IconName;
  size?: number;
  className?: string;
  strokeWidth?: number;
}) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden="true"
      focusable="false"
    >
      {PATHS[name]}
    </svg>
  );
}
