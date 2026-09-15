"use client";

import { useEffect, useState } from "react";
import { api, type MyProfile, type GithubProfile } from "@/lib/api";

export default function ProfilePage() {
  const [profile, setProfile] = useState<MyProfile | null>(null);
  const [github, setGithub] = useState<GithubProfile | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [firstName, setFirstName] = useState("");
  const [lastName, setLastName] = useState("");
  const [mobile, setMobile] = useState("");
  const [savingProfile, setSavingProfile] = useState(false);
  const [profileSaved, setProfileSaved] = useState(false);

  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [passwordError, setPasswordError] = useState<string | null>(null);
  const [passwordSaved, setPasswordSaved] = useState(false);
  const [savingPassword, setSavingPassword] = useState(false);

  const [disconnectingGithub, setDisconnectingGithub] = useState(false);

  async function refresh() {
    try {
      const [p, gh] = await Promise.all([api.me(), api.githubProfile()]);
      setProfile(p);
      setFirstName(p.first_name ?? "");
      setLastName(p.last_name ?? "");
      setMobile(p.mobile_number ?? "");
      setGithub(gh);
      setError(null);
    } catch {
      setError("Could not load your profile.");
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  async function saveProfile(e: React.FormEvent) {
    e.preventDefault();
    setSavingProfile(true);
    setProfileSaved(false);
    try {
      await api.updateMe({ first_name: firstName, last_name: lastName, mobile_number: mobile });
      setProfileSaved(true);
      setTimeout(() => setProfileSaved(false), 1500);
    } finally {
      setSavingProfile(false);
    }
  }

  async function savePassword(e: React.FormEvent) {
    e.preventDefault();
    setSavingPassword(true);
    setPasswordError(null);
    setPasswordSaved(false);
    try {
      await api.changePassword(currentPassword, newPassword);
      setCurrentPassword("");
      setNewPassword("");
      setPasswordSaved(true);
      setTimeout(() => setPasswordSaved(false), 1500);
    } catch (err) {
      setPasswordError(err instanceof Error ? err.message : "Could not change your password.");
    } finally {
      setSavingPassword(false);
    }
  }

  async function disconnectGithub() {
    if (!confirm("Disconnect GitHub? Every repo's automated issues/PRs will stop working until you reconnect.")) return;
    setDisconnectingGithub(true);
    try {
      await api.disconnectGithub();
      await refresh();
    } finally {
      setDisconnectingGithub(false);
    }
  }

  if (error) return <div className="badge badge-red">{error}</div>;
  if (!profile) return <ProfileSkeleton />;

  return (
    <div className="space-y-8 animate-fade-in max-w-2xl">
      <div>
        <h1 className="text-xl font-semibold">Profile</h1>
        <p className="text-sm text-gray-500 mt-1">Your account details and connected apps.</p>
      </div>

      <section className="card p-5">
        <div className="flex items-center gap-3 mb-5">
          <div className="w-12 h-12 rounded-full bg-accent/20 border border-accent/40 text-accent-soft text-lg font-semibold flex items-center justify-center">
            {profile.email[0]?.toUpperCase()}
          </div>
          <div>
            <div className="font-medium">{profile.email}</div>
            <span className={`badge mt-1 ${profile.role === "admin" ? "badge-accent" : "badge-gray"}`}>{profile.role}</span>
          </div>
        </div>

        <form onSubmit={saveProfile} className="space-y-3">
          <div className="grid sm:grid-cols-2 gap-3">
            <div>
              <label className="section-label mb-1.5 block">First name</label>
              <input value={firstName} onChange={(e) => setFirstName(e.target.value)} className="input w-full px-3 py-2 text-sm" />
            </div>
            <div>
              <label className="section-label mb-1.5 block">Last name</label>
              <input value={lastName} onChange={(e) => setLastName(e.target.value)} className="input w-full px-3 py-2 text-sm" />
            </div>
          </div>
          <div>
            <label className="section-label mb-1.5 block">Mobile number</label>
            <input value={mobile} onChange={(e) => setMobile(e.target.value)} placeholder="+1 555 000 0000" className="input w-full px-3 py-2 text-sm" />
          </div>
          <div className="flex items-center gap-3 pt-1">
            <button type="submit" disabled={savingProfile} className="btn btn-primary px-4 py-2 text-sm">
              {savingProfile ? "Saving…" : "Save changes"}
            </button>
            {profileSaved && <span className="text-xs text-accent-soft">Saved</span>}
          </div>
        </form>
      </section>

      <section className="card p-5">
        <h2 className="font-semibold mb-4">Connected apps</h2>
        <div className="space-y-3">
          {github?.connected ? (
            <div className="flex items-center justify-between p-3.5 rounded-lg bg-green-950/20 border border-green-900/40">
              <div className="flex items-center gap-2.5">
                {github.avatar_url && (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={github.avatar_url} alt="" className="w-8 h-8 rounded-full" />
                )}
                <div>
                  <div className="text-sm font-medium">GitHub</div>
                  <div className="text-xs text-gray-500">@{github.login}</div>
                </div>
              </div>
              <button onClick={disconnectGithub} disabled={disconnectingGithub} className="btn btn-danger px-3 py-1.5 text-xs">
                {disconnectingGithub ? "Disconnecting…" : "Disconnect"}
              </button>
            </div>
          ) : (
            <div className="flex items-center justify-between p-3.5 rounded-lg bg-white/[0.02] border border-border">
              <div>
                <div className="text-sm font-medium">GitHub</div>
                <div className="text-xs text-gray-500">Not connected</div>
              </div>
              <a href="/api/github/oauth/start" className="btn btn-primary px-4 py-2 text-xs">
                Connect GitHub
              </a>
            </div>
          )}

          <div className="flex items-center justify-between p-3.5 rounded-lg bg-white/[0.02] border border-border">
            <div>
              <div className="text-sm font-medium">Slack</div>
              <div className="text-xs text-gray-500">Connected per repo — manage from a repo&apos;s settings page</div>
            </div>
            <a href="/connect" className="btn btn-ghost px-3 py-1.5 text-xs">
              Manage repos
            </a>
          </div>
        </div>
      </section>

      <section className="card p-5">
        <h2 className="font-semibold mb-4">Change password</h2>
        <form onSubmit={savePassword} className="space-y-3">
          <div>
            <label className="section-label mb-1.5 block">Current password</label>
            <input
              type="password"
              value={currentPassword}
              onChange={(e) => setCurrentPassword(e.target.value)}
              className="input w-full px-3 py-2 text-sm"
            />
          </div>
          <div>
            <label className="section-label mb-1.5 block">New password</label>
            <input
              type="password"
              minLength={8}
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              placeholder="At least 8 characters"
              className="input w-full px-3 py-2 text-sm"
            />
          </div>
          {passwordError && <p className="text-sm text-red-400">{passwordError}</p>}
          <div className="flex items-center gap-3 pt-1">
            <button
              type="submit"
              disabled={savingPassword || !currentPassword || newPassword.length < 8}
              className="btn btn-primary px-4 py-2 text-sm"
            >
              {savingPassword ? "Updating…" : "Update password"}
            </button>
            {passwordSaved && <span className="text-xs text-accent-soft">Updated</span>}
          </div>
        </form>
      </section>
    </div>
  );
}

function ProfileSkeleton() {
  return (
    <div className="space-y-4 max-w-2xl">
      <div className="skeleton h-8 w-40" />
      <div className="skeleton h-40 w-full" />
      <div className="skeleton h-32 w-full" />
    </div>
  );
}
