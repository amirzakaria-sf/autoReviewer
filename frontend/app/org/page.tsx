"use client";

/* Org administration.
 *
 * Three independent axes per person — permission, expertise, level — shown as
 * three separate controls, because collapsing them into one "role" dropdown
 * is the modelling mistake this page exists to avoid.
 *
 * The routing preview at the bottom is the part that earns trust: nobody
 * believes a routing rule until they have watched it decide something.
 */

import { useCallback, useEffect, useState } from "react";
import { api, type OrgMember, type OrgOverview, type AssignmentPreview } from "@/lib/api";
import { EmptyState, RowSkeleton } from "@/components/EmptyState";
import { useToast } from "@/components/Toast";
import { OrgInvitePanel } from "@/components/OrgInvitePanel";
import { DesignationsPanel, IdentityPanel, RoutingPanel } from "@/components/OrgConfigPanels";

const SENIORITY = ["sde1", "sde2", "sde3", "staff"] as const;
const CATEGORIES = ["ui", "accessibility", "backend", "security", "performance", "documentation"];

export default function OrgPage() {
  const [org, setOrg] = useState<OrgOverview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [savingId, setSavingId] = useState<string | null>(null);
  const toast = useToast();

  const refresh = useCallback(async () => {
    try {
      setOrg(await api.org());
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load your organization.");
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  async function patchMember(member: OrgMember, patch: Record<string, unknown>) {
    setSavingId(member.member_id);
    try {
      const result = await api.updateOrgMember(member.member_id, patch);
      setOrg((current) => (current ? { ...current, members: result.members } : current));
      toast("success", `Updated ${member.name}.`);
    } catch (err) {
      toast("error", err instanceof Error ? err.message : "Could not save that change.");
    } finally {
      setSavingId(null);
    }
  }

  // A user who belongs to no organization is the normal state right after an
  // account is created, not an error -- so it explains what has to happen
  // rather than printing the raw message from the endpoint.
  async function removeMember(member: OrgMember) {
    if (!confirm(`Remove ${member.name} from ${org?.name}? They keep their account.`)) return;
    setSavingId(member.member_id);
    try {
      const result = await api.removeOrgMember(member.member_id);
      setOrg((current) => (current ? { ...current, members: result.members } : current));
      toast("info", `${member.name} removed.`);
    } catch (err) {
      // 409 means the org's state refuses it -- the last admin -- which is an
      // explanation, not a validation error.
      toast("error", err instanceof Error ? err.message : "Could not remove that member.");
    } finally {
      setSavingId(null);
    }
  }

  if (error?.includes("do not belong")) {
    return (
      <EmptyState
        title="You are not in an organization yet"
        hint="An organization owns repositories, members and routing. A system admin creates one and adds you to it, or sends you an invitation by email."
      />
    );
  }
  if (error) return <div className="badge badge-red">{error}</div>;
  if (!org) return <RowSkeleton rows={3} />;

  const isAdmin = org.role === "org_admin";

  return (
    <div className="space-y-8 animate-fade-in">
      <div>
        <h1 className="text-lg font-semibold">{org.name}</h1>
        <p className="text-xs text-lo mt-0.5">
          Who is on the team, what they know, and which findings reach them.
          {!isAdmin && " Only an org admin can change these."}
        </p>
      </div>

      {isAdmin && <OrgInvitePanel designations={org.designations} onMemberJoined={refresh} />}

      {isAdmin && (
        <section className="card p-5">
          <h2 className="font-semibold mb-1">GitHub App installation</h2>
          <p className="text-xs text-lo mb-3">
            Installation id for this organization. Empty uses the process-wide GITHUB_APP_INSTALLATION_ID.
          </p>
          <GitHubInstallField
            value={org.github_app_installation_id || ""}
            onSave={async (v) => {
              await api.updateOrg({ github_app_installation_id: v });
              refresh();
            }}
          />
        </section>
      )}

      <section>
        <h2 className="section-label mb-3">People</h2>
        {org.members.length === 0 ? (
          <EmptyState title="No members yet" hint="Invite someone above to get started." />
        ) : (
          <div className="space-y-2.5">
            {org.members.map((member) => (
              <div key={member.member_id} className="card p-4 space-y-3">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="text-sm font-medium truncate">{member.name}</div>
                    <div className="text-xs text-lo num truncate">{member.email}</div>
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    {savingId === member.member_id && <span className="spinner" aria-label="Saving" />}
                    {isAdmin && (
                      <button
                        onClick={() => removeMember(member)}
                        className="btn btn-ghost px-2.5 py-1 text-xs"
                        title="Remove from this organization"
                      >
                        Remove
                      </button>
                    )}
                  </div>
                </div>

                <div className="grid sm:grid-cols-2 gap-3">
                  <label className="block">
                    <span className="section-label mb-1.5 block">Permission</span>
                    <select
                      id={`role-${member.member_id}`}
                      value={member.role}
                      disabled={!isAdmin}
                      onChange={(event) => patchMember(member, { role: event.target.value })}
                      className="input w-full px-2.5 py-1.5 text-sm"
                    >
                      <option value="member">Member</option>
                      <option value="org_admin">Org admin</option>
                    </select>
                  </label>

                  <label className="block">
                    <span className="section-label mb-1.5 block">Level</span>
                    <select
                      id={`seniority-${member.member_id}`}
                      value={member.seniority}
                      disabled={!isAdmin}
                      onChange={(event) => patchMember(member, { seniority: event.target.value })}
                      className="input w-full px-2.5 py-1.5 text-sm"
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
                  <span className="section-label mb-1.5 block">
                    Areas — findings in these are routed here
                  </span>
                  <div className="flex flex-wrap gap-1.5">
                    {org.designations.map((designation) => {
                      const held = member.designations.includes(designation.key);
                      return (
                        <button
                          key={designation.key}
                          disabled={!isAdmin}
                          onClick={() =>
                            patchMember(member, {
                              designations: held
                                ? member.designations.filter((key) => key !== designation.key)
                                : [...member.designations, designation.key],
                            })
                          }
                          className={`badge transition ${held ? "badge-amber" : "badge-gray"}`}
                          style={{ cursor: isAdmin ? "pointer" : "default" }}
                        >
                          {designation.label}
                        </button>
                      );
                    })}
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      <DesignationsPanel
        designations={org.designations}
        isAdmin={isAdmin}
        onChanged={(next) => setOrg((current) => (current ? { ...current, designations: next } : current))}
      />

      <RoutingPanel
        rules={org.routing_rules}
        designations={org.designations}
        isAdmin={isAdmin}
        onChanged={(next) => setOrg((current) => (current ? { ...current, routing_rules: next } : current))}
      />

      <IdentityPanel members={org.members} isAdmin={isAdmin} />

      <RoutingPreview categories={CATEGORIES} />
    </div>
  );
}

/* Nobody trusts routing rules until they have watched them decide something.
   This answers "who would get this?" before a real finding depends on it. */
function RoutingPreview({ categories }: { categories: string[] }) {
  const [category, setCategory] = useState("ui");
  const [severity, setSeverity] = useState(3);
  const [author, setAuthor] = useState("");
  const [result, setResult] = useState<AssignmentPreview | null>(null);
  const [running, setRunning] = useState(false);

  async function run() {
    setRunning(true);
    try {
      setResult(await api.previewAssignment({ category, severity, author }));
    } finally {
      setRunning(false);
    }
  }

  return (
    <section className="card p-5 space-y-4">
      <div>
        <h2 className="font-semibold text-sm">Who would get this?</h2>
        <p className="text-xs text-lo mt-0.5">
          Runs the real routing pipeline without needing a real finding.
        </p>
      </div>

      <div className="grid sm:grid-cols-3 gap-3">
        <label className="block">
          <span className="section-label mb-1.5 block">Category</span>
          <select
            id="preview-category"
            value={category}
            onChange={(event) => setCategory(event.target.value)}
            className="input w-full px-2.5 py-1.5 text-sm"
          >
            {categories.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
        </label>
        <label className="block">
          <span className="section-label mb-1.5 block">Severity</span>
          <select
            id="preview-severity"
            value={severity}
            onChange={(event) => setSeverity(Number(event.target.value))}
            className="input w-full px-2.5 py-1.5 text-sm"
          >
            {[1, 2, 3, 4, 5].map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </label>
        <label className="block">
          <span className="section-label mb-1.5 block">Git author (optional)</span>
          <input
            id="preview-author"
            value={author}
            onChange={(event) => setAuthor(event.target.value)}
            placeholder="dev@company.com"
            className="input w-full px-2.5 py-1.5 text-sm"
          />
        </label>
      </div>

      <button onClick={run} disabled={running} className="btn btn-primary px-4 py-2">
        {running ? "Working…" : "Preview"}
      </button>

      {result && (
        <div className="space-y-2 pt-1">
          <div className="flex items-center gap-2">
            <span className="section-label">Owner</span>
            <span className="text-sm font-medium">
              {result.assignee ? result.assignee.name : "nobody"}
            </span>
            {result.assignee && (
              <span className="badge badge-gray num">{result.assignee.seniority.toUpperCase()}</span>
            )}
          </div>
          <ol className="space-y-1">
            {result.reasoning.map((line, index) => (
              <li key={index} className="text-xs text-mid flex gap-2">
                <span className="num text-lo">{index + 1}</span>
                {line}
              </li>
            ))}
          </ol>
          {result.watchers.length > 0 && (
            <p className="text-xs text-lo">
              Watching (notified, not on the hook): {result.watchers.map((w) => w.name).join(", ")}
            </p>
          )}
        </div>
      )}
    </section>
  );
}

function GitHubInstallField({ value, onSave }: { value: string; onSave: (v: string) => void }) {
  const [local, setLocal] = useState(value);
  useEffect(() => setLocal(value), [value]);
  return (
    <input
      className="w-full bg-white/[0.04] border border-border rounded-lg px-3 py-2 text-sm num"
      value={local}
      placeholder="Installation id"
      onChange={(e) => setLocal(e.target.value)}
      onBlur={() => {
        if (local !== value) onSave(local.trim());
      }}
      onKeyDown={(e) => {
        if (e.key === "Enter" && local !== value) onSave(local.trim());
      }}
    />
  );
}
