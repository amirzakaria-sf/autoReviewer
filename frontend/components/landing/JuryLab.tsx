"use client";

import { useState } from "react";
import { Icon } from "@/components/Icon";
import { CATEGORIES, DISAGREEMENT_THRESHOLD, type CategoryKey, CATEGORY } from "./sim-data";

/* The routing rule, made touchable. The arithmetic is the backend's own
 * (`_juries_disagree` and `route_on_score` in app/graphs/bug_council.py): the
 * Skeptic reports confidence that it is NOT a bug, so it is flipped before
 * the two are compared. */

export function JuryLab() {
  const [skeptic, setSkeptic] = useState(22);
  const [corroborator, setCorroborator] = useState(90);
  const [score, setScore] = useState(82);
  const [key, setKey] = useState<CategoryKey>("security");
  const category = CATEGORY[key];

  const skepticIsBug = 100 - skeptic;
  const gap = Math.abs(skepticIsBug - corroborator);
  const audit = gap > DISAGREEMENT_THRESHOLD;
  const raised = score >= category.assurance;

  return (
    <div className="lp-lab">
      <div className="card p-5">
        <p className="lp-k">1 · The jury</p>
        <Slider label="Skeptic: confident it is NOT a bug" value={skeptic} set={setSkeptic} color="var(--pending)" />
        <Slider label="Corroborator: confident it IS a bug" value={corroborator} set={setCorroborator} color="var(--amber)" />
        <div className="lp-gap">
          <div className="lp-gap-track">
            <span className="lp-gap-mark" style={{ left: `${skepticIsBug}%`, background: "var(--pending)" }} title="Skeptic, flipped" />
            <span className="lp-gap-mark" style={{ left: `${corroborator}%`, background: "var(--amber)" }} title="Corroborator" />
            <span
              className="lp-gap-span"
              style={{
                left: `${Math.min(skepticIsBug, corroborator)}%`,
                width: `${gap}%`,
                background: audit ? "var(--amber-glow)" : "rgba(47,212,143,0.12)",
                borderColor: audit ? "var(--amber)" : "var(--verified)",
              }}
            />
          </div>
          <p className="text-[12px] text-mid mt-2 leading-relaxed">
            On one scale, both say <em>is a bug</em> with <span className="num text-hi">{skepticIsBug}</span> and{" "}
            <span className="num text-hi">{corroborator}</span>. The gap is <span className="num text-hi">{gap}</span>.
          </p>
        </div>
        <div className={`lp-verdict ${audit ? "warn" : "ok"}`}>
          <Icon name={audit ? "alert" : "check"} size={16} />
          {audit
            ? `Over ${DISAGREEMENT_THRESHOLD}: the Meta-Auditor is called, on the strongest model, to rule between them.`
            : `Within ${DISAGREEMENT_THRESHOLD}: the jury broadly agrees, and the Arbiter's score stands.`}
        </div>
      </div>

      <div className="card p-5">
        <p className="lp-k">2 · The bar</p>
        <div className="flex flex-wrap gap-1.5 mt-2" role="radiogroup" aria-label="Category">
          {CATEGORIES.map((c) => (
            <button
              key={c.key}
              type="button"
              role="radio"
              aria-checked={c.key === key}
              className={`lp-chip-btn ${c.key === key ? "on" : ""}`}
              onClick={() => setKey(c.key)}
            >
              <Icon name={c.icon} size={13} /> {c.label} <span className="num text-lo">{c.assurance}</span>
            </button>
          ))}
        </div>
        <Slider label="Arbiter's assurance score" value={score} set={setScore} color={raised ? "var(--verified)" : "var(--failed)"} />
        <div className="lp-bar lp-gauge mt-1">
          <span style={{ width: `${score}%`, background: raised ? "var(--verified)" : "var(--failed)" }} />
          <i style={{ left: `${category.assurance}%` }} aria-hidden />
        </div>
        <div className={`lp-verdict ${raised ? "ok" : "hold"}`}>
          <Icon name={raised ? "alert" : "halt"} size={16} />
          {raised
            ? `${score} clears ${category.label}'s bar of ${category.assurance}: a GitHub issue is raised and a fix is attempted.`
            : `${score} is under ${category.label}'s bar of ${category.assurance}: held. No issue, no page, no noise.`}
        </div>
        <p className="text-[11px] text-lo mt-3 leading-relaxed">
          Bars differ on purpose. A leaked credential has to clear {CATEGORY.security.assurance}; a stale README only{" "}
          {CATEGORY.documentation.assurance}. The fix then faces its own, higher resolution bar ({category.resolution} here).
        </p>
      </div>
    </div>
  );
}

function Slider({ label, value, set, color }: { label: string; value: number; set: (v: number) => void; color: string }) {
  return (
    <label className="block mt-4">
      <span className="flex justify-between text-[12px]">
        <span className="text-mid">{label}</span>
        <span className="num text-hi">{value}</span>
      </span>
      <input
        type="range"
        min={0}
        max={100}
        value={value}
        onChange={(event) => set(Number(event.target.value))}
        className="lp-range"
        style={{ ["--c" as string]: color, ["--v" as string]: `${value}%` }}
      />
    </label>
  );
}
