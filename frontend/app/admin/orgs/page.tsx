"use client";

/* Organizations, for a system admin.
 *
 * This page exists because nothing in the product could create an
 * organization: the first one, its members and its routing rules were all
 * inserted by hand, so a second company could not exist at all.
 *
 * Naming an admin at creation time is the part that matters. An org with no
 * admin can invite nobody and configure nothing, so the form treats it as the
 * normal path and warns plainly when it is skipped.
 */

import { useCallback, useEffect, useState } from "react";
import { api, type OrgSummary } from "@/lib/api";
import { EmptyState, RowSkeleton } from "@/components/EmptyState";
import { useToast } from "@/components/Toast";

function slugify(name: string) {
  return name.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 48);
}

export default function AdminOrgsPage() {
  const [orgs, setOrgs] = useState<OrgSummary[] | null>(null);
  const [name, setName] = useState("");
  const [adminEmail, setAdminEmail] = useState("");
  const [creating, setCreating] = useState(false);
  const [inviteLink, setInviteLink] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [rescue, setRescue] = useState<Record<string, string>>({});
  const toast = useToast();

  const refresh = useCallback(async () => {
    try {
      setOrgs(await api.adminOrgs());
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load organizations.");
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  async function create(event: React.FormEvent) {
    event.preventDefault();
    if (!name.trim()) return;
    setCreating(true);
    setInviteLink(null);
    try {
      const result = await api.createOrg({ name: name.trim(), admin_email: adminEmail.trim() });
      setName("");
      setAdminEmail("");
      if (result.warning) {
        toast("info", result.warning);
      } else if (result.admin) {
        toast("success", `${result.org.name} created — ${result.admin} is its admin.`);
      } else {
        toast("success", `${result.org.name} created — invitation sent.`);
      }
      // Shown rather than hidden: with no SMTP configured the invitation
      // exists and reaches nobody, which looks exactly like a broken feature.
      if (result.invite_link) setInviteLink(result.invite_link);
      await refresh();
    } catch (err) {
      toast("error", err instanceof Error ? err.message : "Could not create that organization.");
    } finally {
      setCreating(false);
    }
  }

  async function nameAdmin(orgId: string) {
    const email = (rescue[orgId] ?? "").trim();
    if (!email) return;
    try {
      const result = await api.addOrgAdmin(orgId, email);
      setRescue((current) => ({ ...current, [orgId]: "" }));
      toast("success", result.admin ? `${result.admin} is now an admin.` : `Invitation sent to ${email}.`);
      if (result.invite_link) setInviteLink(result.invite_link);
      await refresh();
    } catch (err) {
      toast("error", err instanceof Error ? err.message : "Could not name that admin.");
    }
  }

  const previewSlug = slugify(name);

  return (
    <div className="space-y-6 animate-fade-in pb-20">
      <header>
        <h1 className="text-lg font-semibold">Organizations</h1>
        <p className="text-sm text-mid mt-1.5 leading-relaxed max-w-2xl">
          An organization owns repositories, members and routing. Create one here and name its first
          admin; from there it administers itself.
        </p>
      </header>

      <section className="card p-5">
        <h2 className="section-label mb-4">New organization</h2>
        <form onSubmit={create} className="space-y-4">
          <div className="grid sm:grid-cols-2 gap-4">
            <label className="block">
              <span className="section-label">Name</span>
              <input
                id="org-name"
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder="Acme Rockets"
                className="w-full mt-1.5 rounded-lg px-3 py-2 text-sm"
                style={{ background: "var(--ink-900)", border: "1px solid var(--ink-700)", color: "var(--text-hi)" }}
              />
              {previewSlug && (
                <span className="text-[11px] text-lo num mt-1.5 block">
                  slug <span className="text-mid">{previewSlug}</span> — also the workspace directory, and
                  permanent
                </span>
              )}
            </label>
            <label className="block">
              <span className="section-label">First admin (email)</span>
              <input
                id="org-admin-email"
                value={adminEmail}
                onChange={(event) => setAdminEmail(event.target.value)}
                placeholder="lead@acme.com"
                className="w-full mt-1.5 rounded-lg px-3 py-2 text-sm"
                style={{ background: "var(--ink-900)", border: "1px solid var(--ink-700)", color: "var(--text-hi)" }}
              />
              <span className="text-[11px] text-lo mt-1.5 block leading-relaxed">
                Added straight away if they already have an account, invited by email if not.
              </span>
            </label>
          </div>
          <button disabled={creating || !name.trim()} className="btn btn-primary px-5 py-2.5">
            {creating ? "Creating…" : "Create organization"}
          </button>
        </form>

        {inviteLink && (
          <div
            className="mt-4 rounded-lg p-3.5 text-xs leading-relaxed"
            style={{ background: "var(--ink-900)", border: "1px solid var(--amber-dim, var(--ink-700))" }}
          >
            <div className="section-label mb-1.5">Invitation link</div>
            <p className="text-mid mb-2">
              Send this to the admin if they do not receive the email. It expires in 7 days.
            </p>
            <code className="num break-all text-[11px] text-hi">{inviteLink}</code>
          </div>
        )}
      </section>

      {error && <p className="text-sm text-failed">{error}</p>}
      {!orgs && !error && <RowSkeleton rows={3} />}

      {orgs && orgs.length === 0 && (
        <EmptyState
          title="No organizations yet"
          hint="Create the first one above. Until an organization exists, nobody can connect a repository."
        />
      )}

      {orgs && orgs.length > 0 && (
        <section className="card overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left" style={{ borderBottom: "1px solid var(--ink-700)" }}>
                <th className="px-3.5 py-2.5 section-label">Organization</th>
                <th className="px-3.5 py-2.5 section-label">Slug</th>
                <th className="px-3.5 py-2.5 section-label">Members</th>
                <th className="px-3.5 py-2.5 section-label">Repos</th>
                <th className="px-3.5 py-2.5 section-label">Pending invites</th>
              </tr>
            </thead>
            <tbody>
              {orgs.map((org) => (
                <tr key={org.id} className="hairline">
                  <td className="px-3.5 py-2.5 font-medium">{org.name}</td>
                  <td className="px-3.5 py-2.5 num text-mid">{org.slug}</td>
                  <td className="px-3.5 py-2.5 num">{org.member_count}</td>
                  <td className="px-3.5 py-2.5 num text-mid">{org.repo_count}</td>
                  <td className="px-3.5 py-2.5 num text-mid">
                    {org.pending_invites || <span className="text-lo">—</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      {orgs?.filter((org) => org.member_count === 0).map((org) => (
        <section key={org.id} className="card p-5" style={{ borderColor: "var(--amber-dim, var(--ink-700))" }}>
          <h2 className="section-label mb-1.5">{org.name} has no admin</h2>
          <p className="text-xs text-mid mb-4 leading-relaxed">
            Every one of an organization&apos;s own settings requires an org admin, so this one can invite
            nobody and configure nothing until it has one.
          </p>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              nameAdmin(org.id);
            }}
            className="flex gap-2 flex-wrap"
          >
            <input
              id={`rescue-${org.id}`}
              value={rescue[org.id] ?? ""}
              onChange={(event) => setRescue((current) => ({ ...current, [org.id]: event.target.value }))}
              placeholder="admin@company.com"
              className="rounded-lg px-3 py-2 text-sm flex-1 min-w-[220px]"
              style={{ background: "var(--ink-900)", border: "1px solid var(--ink-700)", color: "var(--text-hi)" }}
            />
            <button disabled={!(rescue[org.id] ?? "").trim()} className="btn btn-primary px-4 py-2 text-sm">
              Make them admin
            </button>
          </form>
        </section>
      ))}
    </div>
  );
}
