"use client";

import { useState } from "react";
import { Icon, type IconName } from "@/components/Icon";

/* Same bug, two Fridays. A range input drives a clip-path, so the control is
 * a real slider: keyboard, touch and screen readers all work without a
 * hand-rolled drag handler. */

const BEFORE: { icon: IconName; text: string }[] = [
  { icon: "alert", text: "A customer emails: their order total is wrong. It's 5:40 p.m." },
  { icon: "search", text: "Reproduce it. Can't. Reproduce it again with three items. There it is." },
  { icon: "terminal", text: "Grep for the total. Four functions compute something called total." },
  { icon: "merge", text: "Patch the one that looks right. CI is green. CI was green before, too." },
  { icon: "deploy", text: "Ship it straight to production, because the preview environment is broken." },
  { icon: "alert", text: "Monday: the totals are right now, but the discount is applied twice." },
];

const AFTER: { icon: IconName; text: string }[] = [
  { icon: "search", text: "The push that broke it fails a detector minutes after it lands." },
  { icon: "scales", text: "A Skeptic tries to explain it away. A Corroborator checks the UI and API agree. Both on the record." },
  { icon: "merge", text: "A patch at the root cause, rejected once by the verifier, then passing the same check." },
  { icon: "deploy", text: "Deployed to its own preview and checked again there, against the live URL." },
  { icon: "approve", text: "A notification with the diff and the evidence. You approve it from your phone." },
  { icon: "check", text: "A pull request waits for your merge. Nobody's weekend changes." },
];

export function BeforeAfter() {
  const [position, setPosition] = useState(50);
  return (
    <div className="lp-compare" style={{ ["--pos" as string]: `${position}%` }}>
      <div className="lp-cmp-pane lp-cmp-before">
        <p className="lp-cmp-label bad">Without WhipGuard</p>
        <ol>
          {BEFORE.map((item) => (
            <li key={item.text}>
              <Icon name={item.icon} size={16} className="shrink-0 mt-0.5" />
              <span>{item.text}</span>
            </li>
          ))}
        </ol>
      </div>
      <div className="lp-cmp-pane lp-cmp-after" aria-hidden={position > 96}>
        <p className="lp-cmp-label ok">With WhipGuard</p>
        <ol>
          {AFTER.map((item) => (
            <li key={item.text}>
              <Icon name={item.icon} size={16} className="shrink-0 mt-0.5" />
              <span>{item.text}</span>
            </li>
          ))}
        </ol>
      </div>
      <input
        className="lp-cmp-range"
        type="range"
        min={0}
        max={100}
        value={position}
        onChange={(event) => setPosition(Number(event.target.value))}
        aria-label="Compare without and with WhipGuard"
      />
      <div className="lp-cmp-handle" aria-hidden>
        <span>
          <Icon name="arrow" size={14} className="rotate-180" />
          <Icon name="arrow" size={14} />
        </span>
      </div>
    </div>
  );
}
