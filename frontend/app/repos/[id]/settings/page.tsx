"use client";

import { use, useEffect, useState } from "react";
import { api, type RepoSettings, type CategorySetting } from "@/lib/api";

const ASK_MODES: { key: RepoSettings["ask_mode"]; label: string; detail: string }[] = [
  { key: "autonomous", label: "Autonomous", detail: "Never asks live — holds genuine ambiguity for later review." },
  { key: "balanced", label: "Balanced", detail: "Asks only when the system genuinely can't resolve it itself. Default." },
  { key: "verbose", label: "Verbose", detail: "Asks whenever a clarifying question is available, even at the cost of speed." },
];

export default function RepoSettingsPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [settings, setSettings] = useState<RepoSettings | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  async function refresh() {
    try {
      setSettings(await api.repoSettings(id));
      setError(null);
    } catch {
      setError("Could not load settings for this repo.");
    }
  }

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  async function patch(body: Record<string, unknown>) {
    setSaving(true);
    setSaved(false);
    try {
      await api.updateRepoSettings(id, body);
      await refresh();
      setSaved(true);
      setTimeout(() => setSaved(false), 1500);
    } finally {
      setSaving(false);
    }
  }

  function patchCategory(key: string, field: keyof CategorySetting, value: unknown) {
    patch({ categories: { [key]: { [field]: value } } });
  }


  if (error) return <div className="badge badge-red">{error}</div>;
  if (!settings) return <SettingsSkeleton />;

  return (
    <div className="space-y-8 animate-fade-in">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">{settings.github_full_name}</h1>
          <p className="text-sm text-lo mt-1">Category toggles, thresholds, Ask Mode, and the kill switch.</p>
        </div>
        {saving ? (
          <span className="flex items-center gap-2 text-xs text-lo"><span className="spinner" style={{ marginTop: 0 }} />Saving…</span>
        ) : saved ? (
          <span className="badge badge-green">Saved</span>
        ) : null}
      </div>


      <section className="card p-5" style={{ borderColor: "var(--failed-dim)" }}>
        <h2 className="font-semibold mb-1 flex items-center gap-2">
          <span className="badge badge-red">Kill switch</span>
        </h2>
        <p className="text-xs text-lo mb-4">Not decoration — stops a runaway detector or fix-proposer in one click.</p>
        <div className="grid sm:grid-cols-2 gap-3">
          <ToggleRow
            label="Pause all detection"
            detail="No new bugs will be raised for this repo."
            checked={settings.detection_paused}
            onChange={(v) => patch({ detection_paused: v })}
            danger
          />
          <ToggleRow
            label="Pause all fix proposals"
            detail="Detected bugs won't get automated fixes proposed."
            checked={settings.proposals_paused}
            onChange={(v) => patch({ proposals_paused: v })}
            danger
          />
        </div>
      </section>

      <section className="card p-5">
        <h2 className="font-semibold mb-1">Notifications</h2>
        <p className="text-xs text-lo">
          Slack is connected once for the whole account, and every repo posts to that same channel.
          Set it on your <a href="/profile" className="text-accent hover:underline">profile</a>.
        </p>
      </section>

      <section className="card p-5">
        <h2 className="font-semibold mb-1">Ask Mode</h2>
        <p className="text-xs text-lo mb-4">How eagerly a jury disagreement or a missing-fact escalates to a live question.</p>
        <div className="grid sm:grid-cols-3 gap-3">
          {ASK_MODES.map((mode) => (
            <button
              key={mode.key}
              onClick={() => patch({ ask_mode: mode.key })}
              className={`text-left p-3.5 rounded-lg border transition ${
                settings.ask_mode === mode.key
                  ? "border-accent bg-[rgba(255,178,36,0.1)]"
                  : "border-border hover:border-border2 bg-white/[0.02]"
              }`}
            >
              <div className="text-sm font-medium flex items-center gap-2">
                {mode.label}
                {settings.ask_mode === mode.key && <span className="dot bg-accent" />}
              </div>
              <div className="text-xs text-lo mt-1 leading-relaxed">{mode.detail}</div>
            </button>
          ))}
        </div>
      </section>

      <section>
        <h2 className="section-label mb-3">Categories</h2>
        <div className="space-y-3">
          {settings.categories.map((cat) => (
            <CategoryCard key={cat.key} cat={cat} onPatch={(field, value) => patchCategory(cat.key, field, value)} />
          ))}
        </div>
      </section>
    </div>
  );
}

function CategoryCard({
  cat,
  onPatch,
}: {
  cat: CategorySetting;
  onPatch: (field: keyof CategorySetting, value: unknown) => void;
}) {
  return (
    <div className="card p-4">
      <div className="flex items-center justify-between mb-3">
        <div className="font-medium text-sm">{cat.label}</div>
        <div className="flex items-center gap-4 text-xs">
          <MiniToggle label="Issues" checked={cat.issues_enabled} onChange={(v) => onPatch("issues_enabled", v)} />
          <MiniToggle label="Fixes" checked={cat.fixes_enabled} onChange={(v) => onPatch("fixes_enabled", v)} />
        </div>
      </div>
      <div className="grid sm:grid-cols-2 gap-4">
        <ThresholdSlider
          label="Assurance threshold"
          value={cat.assurance_threshold}
          defaultValue={cat.default_assurance_threshold}
          onChange={(v) => onPatch("assurance_threshold", v)}
        />
        <ThresholdSlider
          label="Resolution threshold"
          value={cat.resolution_threshold}
          defaultValue={cat.default_resolution_threshold}
          onChange={(v) => onPatch("resolution_threshold", v)}
        />
      </div>
    </div>
  );
}

function ThresholdSlider({
  label,
  value,
  defaultValue,
  onChange,
}: {
  label: string;
  value: number;
  defaultValue: number;
  onChange: (v: number) => void;
}) {
  const [local, setLocal] = useState(value);
  useEffect(() => setLocal(value), [value]);

  return (
    <div>
      <div className="flex items-center justify-between text-xs text-lo mb-1.5">
        <span>{label}</span>
        <span className="num text-mid">
          {local}
          {local !== defaultValue && (
            <span className="text-lo"> · default {defaultValue}</span>
          )}
        </span>
      </div>
      <input
        type="range"
        min={0}
        max={100}
        value={local}
        onChange={(e) => setLocal(Number(e.target.value))}
        onMouseUp={() => onChange(local)}
        onTouchEnd={() => onChange(local)}
        className="w-full"
        style={{ accentColor: "var(--amber)" }}
      />
    </div>
  );
}

function ToggleRow({
  label,
  detail,
  checked,
  onChange,
  danger,
}: {
  label: string;
  detail: string;
  checked: boolean;
  onChange: (v: boolean) => void;
  danger?: boolean;
}) {
  return (
    <button
      onClick={() => onChange(!checked)}
      className={`flex items-center justify-between p-3.5 rounded-lg border text-left transition ${
        checked
          ? danger
            ? "border-[color:var(--failed-dim)] bg-[rgba(255,95,86,0.08)]"
            : "border-accent bg-[rgba(255,178,36,0.1)]"
          : "border-border bg-white/[0.02]"
      }`}
    >
      <div>
        <div className="text-sm font-medium">{label}</div>
        <div className="text-xs text-lo mt-0.5">{detail}</div>
      </div>
      <Switch checked={checked} />
    </button>
  );
}

function MiniToggle({ label, checked, onChange }: { label: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <button onClick={() => onChange(!checked)} className="flex items-center gap-1.5 text-mid hover:text-hi transition">
      {label}
      <Switch checked={checked} small />
    </button>
  );
}

function Switch({ checked, small }: { checked: boolean; small?: boolean }) {
  const size = small ? "w-7 h-4" : "w-9 h-5";
  const knob = small ? "w-3 h-3" : "w-4 h-4";
  return (
    <span
      className={`relative inline-block ${size} rounded-full transition-colors shrink-0 ${checked ? "bg-accent" : "bg-white/15"}`}
    >
      <span
        className={`absolute top-0.5 left-0.5 ${knob} rounded-full bg-white transition-transform ${
          checked ? (small ? "translate-x-3" : "translate-x-4") : ""
        }`}
      />
    </span>
  );
}

function SettingsSkeleton() {
  return (
    <div className="space-y-4">
      <div className="skeleton h-8 w-64" />
      <div className="skeleton h-32 w-full" />
      <div className="skeleton h-32 w-full" />
      <div className="skeleton h-24 w-full" />
    </div>
  );
}
