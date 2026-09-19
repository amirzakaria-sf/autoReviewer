"use client";

/* The three settings that were displayed and could not be changed:
 * designations, routing rules, and identity links.
 *
 * Identity links matter most. Blame is the FIRST stage of the assignment
 * pipeline -- git author, then designation, then severity floor -- and with
 * no links configured it never resolved anyone, so every finding fell
 * through to designation routing and the stored trace read "No WhipGuard
 * account is linked to dev@company.com".
 */

import { useCallback, useEffect, useState } from "react";
import {
  api,
  type IdentityOverview,
  type OrgMember,
  type RoutingRule,
} from "@/lib/api";
import { useToast } from "@/components/Toast";

const SENIORITY = ["sde1", "sde2", "sde3", "staff"] as const;
const FIELD = {
  background: "var(--ink-900)",
  border: "1px solid var(--ink-700)",
  color: "var(--text-hi)",
};

type Designation = { id: string; key: string; label: string };

export function DesignationsPanel({
  designations,
  isAdmin,
  onChanged,
}: {
  designations: Designation[];
  isAdmin: boolean;
  onChanged: (next: Designation[]) => void;
}) {
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const toast = useToast();

  async function add(event: React.FormEvent) {
    event.preventDefault();
    if (!name.trim()) return;
    setBusy(true);
    try {
      const result = await api.upsertDesignation(name.trim(), "");
      onChanged(result.designations);
      setName("");
      toast("success", `Added ${name.trim()}.`);
    } catch (err) {
      toast("error", err instanceof Error ? err.message : "Could not add that designation.");
    } finally {
      setBusy(false);
    }
  }

  async function remove(key: string) {
    try {
      const result = await api.deleteDesignation(key);
      onChanged(result.designations);
      toast("info", `Removed ${key}.`);
    } catch (err) {
      // 409 means a routing rule still points here -- an explanation, not a
      // validation error.
      toast("error", err instanceof Error ? err.message : "Could not remove that designation.");
    }
  }

  return (
    <section className="card p-5 space-y-4">
      <div>
        <h2 className="section-label">Areas of expertise</h2>
        <p className="text-xs text-lo mt-1.5 leading-relaxed">
          What people know. Routing rules below point each category at one of these.
        </p>
      </div>

      <div className="flex flex-wrap gap-2">
        {designations.map((designation) => (
          <span key={designation.key} className="badge badge-gray inline-flex items-center gap-1.5">
            {designation.label}
            {isAdmin && (
              <button
                onClick={() => remove(designation.key)}
                className="text-lo hover:text-hi transition"
                aria-label={`Remove ${designation.label}`}
              >
                ×
              </button>
            )}
          </span>
        ))}
        {designations.length === 0 && <span className="text-xs text-lo">None configured.</span>}
      </div>

      {isAdmin && (
        <form onSubmit={add} className="flex gap-2 flex-wrap">
          <input
            id="new-designation"
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="Machine Learning"
            className="rounded-lg px-3 py-2 text-sm flex-1 min-w-[200px]"
            style={FIELD}
          />
          <button disabled={busy || !name.trim()} className="btn btn-ghost px-4 py-2 text-sm">
            Add
          </button>
        </form>
      )}
    </section>
  );
}

export function RoutingPanel({
  rules,
  designations,
  isAdmin,
  onChanged,
}: {
  rules: RoutingRule[];
  designations: Designation[];
  isAdmin: boolean;
  onChanged: (next: RoutingRule[]) => void;
}) {
  const [saving, setSaving] = useState<string | null>(null);
  const toast = useToast();

  async function save(rule: RoutingRule, patch: Partial<RoutingRule>) {
    setSaving(rule.category);
    try {
      const result = await api.setRoutingRule(rule.category, {
        designation_key: patch.designation_key ?? rule.designation_key,
        escalate_at_severity: patch.escalate_at_severity ?? rule.escalate_at_severity,
        min_seniority: patch.min_seniority ?? rule.min_seniority,
      });
      onChanged(result.routing_rules);
    } catch (err) {
      toast("error", err instanceof Error ? err.message : "Could not save that rule.");
    } finally {
      setSaving(null);
    }
  }

  return (
    <section className="card p-5 space-y-4">
      <div>
        <h2 className="section-label">Routing</h2>
        <p className="text-xs text-lo mt-1.5 leading-relaxed">
          Which area owns each category, and the level a severe finding escalates to. These decide who gets
          woken up.
        </p>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left" style={{ borderBottom: "1px solid var(--ink-700)" }}>
              <th className="py-2 pr-3 section-label">Category</th>
              <th className="py-2 pr-3 section-label">Goes to</th>
              <th className="py-2 pr-3 section-label">Escalates at</th>
              <th className="py-2 section-label">Minimum level</th>
            </tr>
          </thead>
          <tbody>
            {rules.map((rule) => (
              <tr key={rule.category} className="hairline">
                <td className="py-2.5 pr-3 num">{rule.category}</td>
                <td className="py-2.5 pr-3">
                  <select
                    id={`route-${rule.category}`}
                    value={rule.designation_key}
                    disabled={!isAdmin || saving === rule.category}
                    onChange={(event) => save(rule, { designation_key: event.target.value })}
                    className="input px-2 py-1 text-xs"
                  >
                    {designations.map((designation) => (
                      <option key={designation.key} value={designation.key}>
                        {designation.label}
                      </option>
                    ))}
                  </select>
                </td>
                <td className="py-2.5 pr-3">
                  <select
                    id={`severity-${rule.category}`}
                    value={rule.escalate_at_severity}
                    disabled={!isAdmin || saving === rule.category}
                    onChange={(event) => save(rule, { escalate_at_severity: Number(event.target.value) })}
                    className="input px-2 py-1 text-xs"
                  >
                    {[1, 2, 3, 4, 5].map((level) => (
                      <option key={level} value={level}>
                        severity ≥ {level}
                      </option>
                    ))}
                  </select>
                </td>
                <td className="py-2.5">
                  <select
                    id={`floor-${rule.category}`}
                    value={rule.min_seniority}
                    disabled={!isAdmin || saving === rule.category}
                    onChange={(event) => save(rule, { min_seniority: event.target.value })}
                    className="input px-2 py-1 text-xs"
                  >
                    {SENIORITY.map((level) => (
                      <option key={level} value={level}>
                        {level.toUpperCase()}
                      </option>
                    ))}
                  </select>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export function IdentityPanel({ members, isAdmin }: { members: OrgMember[]; isAdmin: boolean }) {
  const [data, setData] = useState<IdentityOverview | null>(null);
  const [choice, setChoice] = useState<Record<string, string>>({});
  const [manual, setManual] = useState("");
  const [manualUser, setManualUser] = useState("");
  const toast = useToast();

  const refresh = useCallback(async () => {
    try {
      setData(await api.identityLinks());
    } catch {
      setData({ links: [], unlinked_authors: [] });
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  async function link(externalId: string, userId: string) {
    if (!userId) return;
    try {
      await api.linkIdentity({ user_id: userId, external_id: externalId, provider: "git-email" });
      toast("success", `${externalId} now resolves to a person.`);
      await refresh();
    } catch (err) {
      toast("error", err instanceof Error ? err.message : "Could not link that identity.");
    }
  }

  async function unlink(id: string) {
    try {
      const result = await api.unlinkIdentity(id);
      setData((current) => (current ? { ...current, links: result.links } : current));
      toast("info", "Link removed.");
    } catch (err) {
      toast("error", err instanceof Error ? err.message : "Could not remove that link.");
    }
  }

  return (
    <section className="card p-5 space-y-5">
      <div>
        <h2 className="section-label">Git identities</h2>
        <p className="text-xs text-lo mt-1.5 leading-relaxed">
          The first thing routing tries: whoever last touched the code. Until an address is linked, findings
          skip straight to area-based routing.
        </p>
      </div>

      {data && data.links.length > 0 && (
        <div>
          <div className="section-label mb-2">Linked</div>
          {data.links.map((link) => (
            <div key={link.id} className="flex items-center justify-between gap-3 py-2 hairline first:border-t-0">
              <div className="min-w-0 text-sm">
                <span className="num text-mid">{link.external_id}</span>
                <span className="text-lo mx-2">→</span>
                <span className="truncate">{link.email}</span>
              </div>
              {isAdmin && (
                <button onClick={() => unlink(link.id)} className="btn btn-ghost px-2.5 py-1 text-xs shrink-0">
                  Unlink
                </button>
              )}
            </div>
          ))}
        </div>
      )}

      {data && data.unlinked_authors.length > 0 && isAdmin && (
        <div>
          <div className="section-label mb-1">Seen in your repositories</div>
          <p className="text-[11px] text-lo mb-2 leading-relaxed">
            Addresses that have committed but map to nobody yet.
          </p>
          {data.unlinked_authors.map((author) => (
            <div
              key={author.external_id}
              className="flex items-center justify-between gap-3 py-2 hairline first:border-t-0"
            >
              <div className="min-w-0">
                <div className="text-sm num truncate">{author.external_id}</div>
                <div className="text-[11px] text-lo">{author.commits} commits</div>
              </div>
              <div className="flex gap-2 shrink-0">
                <select
                  id={`link-${author.external_id}`}
                  value={choice[author.external_id] ?? ""}
                  onChange={(event) =>
                    setChoice((current) => ({ ...current, [author.external_id]: event.target.value }))
                  }
                  className="input px-2 py-1 text-xs"
                >
                  <option value="">Who is this?</option>
                  {members.map((member) => (
                    <option key={member.user_id} value={member.user_id}>
                      {member.name}
                    </option>
                  ))}
                </select>
                <button
                  onClick={() => link(author.external_id, choice[author.external_id] ?? "")}
                  disabled={!choice[author.external_id]}
                  className="btn btn-ghost px-2.5 py-1 text-xs"
                >
                  Link
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {isAdmin && (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            link(manual.trim().toLowerCase(), manualUser);
            setManual("");
          }}
          className="flex gap-2 flex-wrap"
        >
          <input
            id="manual-identity"
            value={manual}
            onChange={(event) => setManual(event.target.value)}
            placeholder="another@address.com"
            className="rounded-lg px-3 py-2 text-sm flex-1 min-w-[200px]"
            style={FIELD}
          />
          <select
            id="manual-identity-user"
            value={manualUser}
            onChange={(event) => setManualUser(event.target.value)}
            className="input px-2.5 py-2 text-sm"
          >
            <option value="">Who is this?</option>
            {members.map((member) => (
              <option key={member.user_id} value={member.user_id}>
                {member.name}
              </option>
            ))}
          </select>
          <button disabled={!manual.trim() || !manualUser} className="btn btn-ghost px-4 py-2 text-sm">
            Link
          </button>
        </form>
      )}

      {data && data.links.length === 0 && data.unlinked_authors.length === 0 && (
        <p className="text-xs text-lo leading-relaxed">
          No git history read yet — connect and scan a repository, and the addresses that have committed to it
          appear here.
        </p>
      )}
    </section>
  );
}
