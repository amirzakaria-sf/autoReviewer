"use client";

/* Assurance and resolution CONFIDENCE -- a rubric-weighted 0-100 measure an
   Arbiter assigns, not a raw count of anything, which is why it is named for
   what it expresses rather than called a bare "score".

   The single most important number this product produces, and it was once
   rendered as plain text next to a threshold the reader had to compare by
   hand. The ring draws the comparison instead: the arc is the value, the
   notch is the threshold that decides whether anything happens, and the
   colour is derived from which side of that line it fell on -- so "did this
   clear the bar" is answered before any number is read. */

type Props = {
  score: number | null;
  threshold?: number;
  size?: number;
  label?: string;
};

const STROKE = 6;

export function ScoreRing({ score, threshold, size = 72, label }: Props) {
  const radius = (size - STROKE) / 2;
  const circumference = 2 * Math.PI * radius;
  const value = Math.max(0, Math.min(100, score ?? 0));
  const cleared = threshold === undefined ? null : value >= threshold;

  const stroke =
    score === null ? "var(--ink-600)" : cleared === null ? "var(--amber)" : cleared ? "var(--verified)" : "var(--failed)";

  // The threshold notch, placed on the same circle as the arc. -90deg so 0
  // starts at twelve o'clock rather than at three.
  const notchAngle = threshold === undefined ? null : (threshold / 100) * 360 - 90;
  const notch =
    notchAngle === null
      ? null
      : {
          x1: size / 2 + (radius - STROKE / 2 - 2) * Math.cos((notchAngle * Math.PI) / 180),
          y1: size / 2 + (radius - STROKE / 2 - 2) * Math.sin((notchAngle * Math.PI) / 180),
          x2: size / 2 + (radius + STROKE / 2 + 2) * Math.cos((notchAngle * Math.PI) / 180),
          y2: size / 2 + (radius + STROKE / 2 + 2) * Math.sin((notchAngle * Math.PI) / 180),
        };

  return (
    <div className="inline-flex flex-col items-center gap-1.5" title={threshold !== undefined ? `Threshold ${threshold}` : undefined}>
      <div className="relative" style={{ width: size, height: size }}>
        <svg width={size} height={size} className="-rotate-90">
          <circle
            cx={size / 2} cy={size / 2} r={radius}
            fill="none" stroke="var(--ink-700)" strokeWidth={STROKE}
          />
          <circle
            cx={size / 2} cy={size / 2} r={radius}
            fill="none" stroke={stroke} strokeWidth={STROKE} strokeLinecap="round"
            strokeDasharray={circumference}
            strokeDashoffset={circumference - (value / 100) * circumference}
            style={{ transition: "stroke-dashoffset 0.6s cubic-bezier(0.22, 1, 0.36, 1), stroke 0.3s ease" }}
          />
          {notch && (
            <line
              x1={notch.x1} y1={notch.y1} x2={notch.x2} y2={notch.y2}
              stroke="var(--text-mid)" strokeWidth={2} strokeLinecap="round"
            />
          )}
        </svg>
        <div className="absolute inset-0 grid place-items-center">
          <span className="num font-semibold" style={{ fontSize: size * 0.28, color: stroke }}>
            {score === null ? "—" : value}
          </span>
        </div>
      </div>
      {label && (
        // Wraps to two lines under the ring rather than widening the row it
        // sits in -- "resolution confidence" is a long label next to a 60px
        // circle.
        <span className="section-label text-center leading-tight" style={{ maxWidth: size + 16 }}>
          {label}
        </span>
      )}
    </div>
  );
}
