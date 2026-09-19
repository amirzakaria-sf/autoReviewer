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
  const [disconnectingSlack, setDisconnectingSlack] = useState(false);
  const [testingSlack, setTestingSlack] = useState(false);
  const [notice, setNotice] = useState<{ kind: "ok" | "error"; text: string } | null>(null);

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
    // The Slack OAuth callback lands back here with the outcome in the URL.
    const params = new URLSearchParams(window.location.search);
    if (params.get("slack_connected")) setNotice({ kind: "ok", text: "Slack connected." });
    else if (params.get("slack_error")) setNotice({ kind: "error", text: `Slack connection failed: ${params.get("slack_error")}` });
    if (params.has("slack_connected") || params.has("slack_error")) {
      window.history.replaceState({}, "", window.location.pathname);
    }
  }, []);

  async function disconnectSlack() {
    if (!confirm("Disconnect Slack? Approval requests will stop being posted until you reconnect.")) return;
    setDisconnectingSlack(true);
    try {
      await api.disconnectSlack();
      await refresh();
      setNotice({ kind: "ok", text: "Slack disconnected." });
    } finally {
      setDisconnectingSlack(false);
    }
  }

  async function testSlack() {
    setTestingSlack(true);
    try {
      const result = await api.testSlack();
      setNotice({ kind: "ok", text: `Test message sent to #${result.channel}.` });
    } catch (err) {
      setNotice({ kind: "error", text: err instanceof Error ? err.message : "Could not send a test message." });
    } finally {
      setTestingSlack(false);
    }
  }

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

  const isAdmin = profile?.role === "admin";

  if (error) return <div className="badge badge-red">{error}</div>;
  if (!profile) return <ProfileSkeleton />;

  return (
    <div className="space-y-8 animate-fade-in max-w-2xl">
      <div>
        <h1 className="text-xl font-semibold">Profile</h1>
        <p className="text-sm text-lo mt-1">Your account details and connected apps.</p>
      </div>

      <section className="card p-5">
        <div className="flex items-center gap-3 mb-5">
          <div className="w-12 h-12 rounded-full bg-[rgba(255,178,36,0.14)] border border-[color:var(--amber-dim)] text-accent text-lg font-semibold flex items-center justify-center">
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

      {notice && (
        <div className={`badge ${notice.kind === "ok" ? "badge-green" : "badge-red"}`}>{notice.text}</div>
      )}

      <section className="card p-5">
        <h2 className="font-semibold mb-4">Connected apps</h2>
        <div className="space-y-3">
          {github?.connected ? (
            <div className="flex items-center justify-between p-3.5 rounded-lg bg-[rgba(47,212,143,0.06)] border border-[color:var(--verified-dim)]">
              <div className="flex items-center gap-2.5">
                {github.avatar_url && (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={github.avatar_url} alt="" className="w-8 h-8 rounded-full" />
                )}
                <div>
                  <div className="text-sm font-medium">GitHub</div>
                  <div className="text-xs text-lo">@{github.login}</div>
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
                <div className="text-xs text-lo">Not connected</div>
              </div>
              <a href="/api/github/oauth/start" className="btn btn-primary px-4 py-2 text-xs">
                Connect GitHub
              </a>
            </div>
          )}

          {profile.slack.connected ? (
            <div className="flex items-center justify-between gap-3 p-3.5 rounded-lg bg-[rgba(47,212,143,0.06)] border border-[color:var(--verified-dim)]">
              <div className="min-w-0">
                <div className="text-sm font-medium">Slack</div>
                <div className="text-xs text-lo">
                  Posting to{" "}
                  <span className="num text-mid">
                    {profile.slack.channel_name ? `#${profile.slack.channel_name}` : profile.slack.channel_id}
                  </span>{" "}
                  — every repo notifies this one channel.
                </div>
              </div>
              <div className="flex gap-2 shrink-0">
                {isAdmin && (
                  <button onClick={testSlack} disabled={testingSlack} className="btn btn-ghost px-3 py-1.5 text-xs">
                    {testingSlack ? "Sending…" : "Send test"}
                  </button>
                )}
                {isAdmin && (
                  <a href="/api/slack/oauth/start" className="btn btn-ghost px-3 py-1.5 text-xs">
                    Change channel
                  </a>
                )}
                {isAdmin && (
                  <button onClick={disconnectSlack} disabled={disconnectingSlack} className="btn btn-danger px-3 py-1.5 text-xs">
                    {disconnectingSlack ? "…" : "Disconnect"}
                  </button>
                )}
              </div>
            </div>
          ) : (
            <div className="flex items-center justify-between gap-3 p-3.5 rounded-lg bg-white/[0.02] border border-border">
              <div>
                <div className="text-sm font-medium">Slack</div>
                <div className="text-xs text-lo">
                  {profile.slack.has_token
                    ? "Installed, but no channel picked yet — nothing will be sent."
                    : "Not connected. Approval requests for every repo will go to the channel you pick."}
                </div>
              </div>
              {isAdmin ? (
                <a href="/api/slack/oauth/start" className="btn btn-primary px-4 py-2 text-xs shrink-0">
                  Connect Slack
                </a>
              ) : (
                <span className="badge badge-gray shrink-0">Admin sets this up</span>
              )}
            </div>
          )}
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
          {passwordError && <p className="text-sm text-[color:var(--failed)]">{passwordError}</p>}
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
