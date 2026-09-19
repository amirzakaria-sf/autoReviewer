"use client";

/* Inviting a colleague into the organization.
 *
 * The role, seniority and designations are chosen HERE rather than after the
 * person joins, so accepting produces a configured member instead of someone
 * an admin then has to set up a second time.
 */

import { useCallback, useEffect, useState } from "react";
import { api, type OrgInvite } from "@/lib/api";
import { useToast } from "@/components/Toast";

const SENIORITY = ["sde1", "sde2", "sde3", "staff"] as const;

export function OrgInvitePanel({
  designations,
  onMemberJoined,
}: {
  designations: { key: string; label: string }[];
  onMemberJoined?: () => void;
}) {
  const [invites, setInvites] = useState<OrgInvite[] | null>(null);
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [role, setRole] = useState("member");
  const [seniority, setSeniority] = useState("sde2");
  const [picked, setPicked] = useState<string[]>([]);
  const [sending, setSending] = useState(false);
  const [link, setLink] = useState<string | null>(null);
  const toast = useToast();

  const refresh = useCallback(async () => {
    try {
      setInvites(await api.orgInvites());
    } catch {
      setInvites([]);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  async function send(event: React.FormEvent) {
    event.preventDefault();
    if (!email.trim()) return;
    setSending(true);
    setLink(null);
    try {
      const result = await api.inviteToOrg({
        email: email.trim(),
        name: name.trim(),
        role,
        seniority,
        designations: picked,
      });
      setEmail("");
      setName("");
      setPicked([]);
      toast(
        result.email_sent ? "success" : "info",
        result.email_sent
          ? `Invitation sent to ${result.invite.email}.`
          : `Invitation created for ${result.invite.email} — email is not configured, so send the link yourself.`,
      );
      if (!result.email_sent) setLink(result.link);
      await refresh();
      onMemberJoined?.();
    } catch (err) {
      toast("error", err instanceof Error ? err.message : "Could not send that invitation.");
    } finally {
      setSending(false);
    }
  }

  async function revoke(invite: OrgInvite) {
    try {
      await api.revokeOrgInvite(invite.id);
      toast("info", `Invitation to ${invite.email} withdrawn.`);
      await refresh();
    } catch (err) {
      toast("error", err instanceof Error ? err.message : "Could not withdraw that invitation.");
    }
  }

  const fieldStyle = {
    background: "var(--ink-900)",
    border: "1px solid var(--ink-700)",
    color: "var(--text-hi)",
  };

  return (
    <section className="card p-5 space-y-5">
      <div>
        <h2 className="section-label">Invite a team member</h2>
        <p className="text-xs text-lo mt-1.5 leading-relaxed">
          They get an email with a link. If they already have an account they simply join; if not, they
          set a password first.
        </p>
      </div>

      <form onSubmit={send} className="space-y-4">
        <div className="grid sm:grid-cols-2 gap-3">
          <input
            id="invite-email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            placeholder="dev@acme.com"
            className="rounded-lg px-3 py-2 text-sm"
            style={fieldStyle}
          />
          <input
            id="invite-name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="Name (optional)"
            className="rounded-lg px-3 py-2 text-sm"
            style={fieldStyle}
          />
        </div>

        <div className="grid sm:grid-cols-2 gap-3">
          <label className="block">
            <span className="section-label">Role</span>
            <select
              id="invite-role"
              value={role}
              onChange={(event) => setRole(event.target.value)}
              className="w-full mt-1.5 rounded-lg px-3 py-2 text-sm"
              style={fieldStyle}
            >
              <option value="member">Member</option>
              <option value="org_admin">Org admin</option>
            </select>
          </label>
          <label className="block">
            <span className="section-label">Seniority</span>
            <select
              id="invite-seniority"
              value={seniority}
              onChange={(event) => setSeniority(event.target.value)}
              className="w-full mt-1.5 rounded-lg px-3 py-2 text-sm"
              style={fieldStyle}
            >
              {SENIORITY.map((level) => (
                <option key={level} value={level}>
                  {level.toUpperCase()}
                </option>
              ))}
            </select>
          </label>
        </div>

        <div>
          <span className="section-label">Designations</span>
          <p className="text-[11px] text-lo mt-1 mb-2 leading-relaxed">
            What they know. Findings in a matching category route to them.
          </p>
          <div className="flex flex-wrap gap-2">
            {designations.map((designation) => {
              const on = picked.includes(designation.key);
              return (
                <button
                  key={designation.key}
                  type="button"
                  onClick={() =>
                    setPicked((current) =>
                      on ? current.filter((k) => k !== designation.key) : [...current, designation.key],
                    )
                  }
                  className={`badge ${on ? "badge-accent" : "badge-gray"}`}
                >
                  {designation.label}
                </button>
              );
            })}
            {designations.length === 0 && (
              <span className="text-xs text-lo">This organization has no designations configured.</span>
            )}
          </div>
        </div>

        <button disabled={sending || !email.trim()} className="btn btn-primary px-5 py-2.5">
          {sending ? "Sending…" : "Send invitation"}
        </button>
      </form>

      {link && (
        <div className="rounded-lg p-3.5" style={{ background: "var(--ink-900)", border: "1px solid var(--ink-700)" }}>
          <div className="section-label mb-1.5">Invitation link</div>
          <code className="num break-all text-[11px] text-hi">{link}</code>
        </div>
      )}

      {invites && invites.length > 0 && (
        <div>
          <div className="section-label mb-2">Pending ({invites.length})</div>
          <div>
            {invites.map((invite) => (
              <div key={invite.id} className="flex items-center justify-between gap-3 py-2.5 hairline first:border-t-0">
                <div className="min-w-0">
                  <div className="text-sm truncate">{invite.email}</div>
                  <div className="text-[11px] text-lo mt-0.5">
                    {invite.role === "org_admin" ? "Org admin" : "Member"} · {invite.seniority.toUpperCase()}
                    {invite.designations.length > 0 && ` · ${invite.designations.join(", ")}`}
                  </div>
                </div>
                <button onClick={() => revoke(invite)} className="btn btn-ghost px-2.5 py-1 text-xs shrink-0">
                  Withdraw
                </button>
              </div>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}
