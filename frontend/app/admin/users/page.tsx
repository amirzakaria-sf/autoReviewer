"use client";

import { useEffect, useState } from "react";
import { api, type AccessRequest, type AdminUser } from "@/lib/api";
import { useSession } from "@/components/AuthGate";

export default function AdminUsersPage() {
  const [requests, setRequests] = useState<AccessRequest[]>([]);
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [rejecting, setRejecting] = useState<string | null>(null);
  const [rejectReason, setRejectReason] = useState("");
  const session = useSession();

  async function refresh() {
    try {
      const [reqs, us] = await Promise.all([api.accessRequests(), api.adminUsers()]);
      setRequests(reqs);
      setUsers(us);
      setError(null);
    } catch {
      setError("Could not reach the admin API.");
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  async function approve(id: string) {
    setBusy(id);
    try {
      await api.approveAccessRequest(id);
      await refresh();
    } finally {
      setBusy(null);
    }
  }

  async function reject(id: string) {
    setBusy(id);
    try {
      await api.rejectAccessRequest(id, rejectReason.trim());
      setRejecting(null);
      setRejectReason("");
      await refresh();
    } finally {
      setBusy(null);
    }
  }

  async function toggleRole(user: AdminUser) {
    setBusy(user.id);
    try {
      await api.setUserRole(user.id, user.role === "admin" ? "member" : "admin");
      await refresh();
    } finally {
      setBusy(null);
    }
  }

  async function toggleActive(user: AdminUser) {
    setBusy(user.id);
    try {
      await (user.status === "active" ? api.deactivateUser(user.id) : api.reactivateUser(user.id));
      await refresh();
    } finally {
      setBusy(null);
    }
  }

  const pending = requests.filter((r) => r.status === "pending");
  const decided = requests.filter((r) => r.status !== "pending");

  return (
    <div className="space-y-8 animate-fade-in">
      <div>
        <h1 className="text-xl font-semibold">Users</h1>
        <p className="text-sm text-gray-500 mt-1">Review access requests and manage existing accounts.</p>
      </div>

      {error && <div className="badge badge-red">{error}</div>}

      <section className="card p-5 border-yellow-800/40">
        <h2 className="font-semibold mb-3 flex items-center gap-2">
          <span className="badge badge-yellow">Pending requests</span>
          {pending.length}
        </h2>
        {pending.length === 0 && <p className="text-sm text-gray-500">Nothing waiting on review.</p>}
        <div className="space-y-3">
          {pending.map((r) => (
            <div key={r.id} className="p-3.5 rounded-lg bg-white/[0.03] space-y-2.5">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <div className="text-sm font-medium">{r.name}</div>
                  <div className="text-xs text-gray-500">{r.email}</div>
                </div>
                <div className="text-xs text-gray-600 shrink-0">
                  {r.created_at ? new Date(r.created_at).toLocaleString() : ""}
                </div>
              </div>
              <p className="text-sm text-gray-400 leading-relaxed">{r.reason}</p>

              {rejecting === r.id ? (
                <div className="flex gap-2 pt-1">
                  <input
                    value={rejectReason}
                    onChange={(e) => setRejectReason(e.target.value)}
                    placeholder="Reason (optional)"
                    className="input flex-1 px-2.5 py-1.5 text-xs"
                    autoFocus
                  />
                  <button onClick={() => reject(r.id)} disabled={busy === r.id} className="btn btn-danger px-3 py-1.5 text-xs">
                    Confirm decline
                  </button>
                  <button onClick={() => setRejecting(null)} className="btn btn-ghost px-3 py-1.5 text-xs">
                    Cancel
                  </button>
                </div>
              ) : (
                <div className="flex gap-2 pt-1">
                  <button onClick={() => approve(r.id)} disabled={busy === r.id} className="btn btn-primary px-3 py-1.5 text-xs">
                    Approve
                  </button>
                  <button
                    onClick={() => {
                      setRejecting(r.id);
                      setRejectReason("");
                    }}
                    disabled={busy === r.id}
                    className="btn btn-danger px-3 py-1.5 text-xs"
                  >
                    Decline
                  </button>
                </div>
              )}
            </div>
          ))}
        </div>
      </section>

      {decided.length > 0 && (
        <section>
          <h2 className="section-label mb-3">Past requests</h2>
          <div className="border border-border rounded-lg overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-white/5 text-gray-500 text-left text-xs">
                <tr>
                  <th className="px-3 py-2 font-medium">Name</th>
                  <th className="px-3 py-2 font-medium">Email</th>
                  <th className="px-3 py-2 font-medium">Status</th>
                  <th className="px-3 py-2 font-medium">Decided</th>
                </tr>
              </thead>
              <tbody>
                {decided.map((r) => (
                  <tr key={r.id} className="border-t border-border">
                    <td className="px-3 py-2">{r.name}</td>
                    <td className="px-3 py-2 text-gray-400">{r.email}</td>
                    <td className="px-3 py-2">
                      <span className={`badge ${r.status === "approved" ? "badge-green" : "badge-red"}`}>{r.status}</span>
                      {r.decision_reason && <span className="text-xs text-gray-500 ml-2">{r.decision_reason}</span>}
                    </td>
                    <td className="px-3 py-2 text-gray-500 text-xs">{r.decided_at ? new Date(r.decided_at).toLocaleString() : ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      <section>
        <h2 className="section-label mb-3">Accounts</h2>
        <div className="border border-border rounded-lg overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-white/5 text-gray-500 text-left text-xs">
              <tr>
                <th className="px-3 py-2 font-medium">Email</th>
                <th className="px-3 py-2 font-medium">Role</th>
                <th className="px-3 py-2 font-medium">Status</th>
                <th className="px-3 py-2 font-medium">Last login</th>
                <th className="px-3 py-2 font-medium" />
              </tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.id} className="border-t border-border">
                  <td className="px-3 py-2">{u.email}</td>
                  <td className="px-3 py-2">
                    <span className={`badge ${u.role === "admin" ? "badge-accent" : "badge-gray"}`}>{u.role}</span>
                  </td>
                  <td className="px-3 py-2">
                    <span className={`badge ${u.status === "active" ? "badge-green" : "badge-gray"}`}>{u.status}</span>
                  </td>
                  <td className="px-3 py-2 text-gray-500 text-xs">
                    {u.last_login_at ? new Date(u.last_login_at).toLocaleString() : "never"}
                  </td>
                  <td className="px-3 py-2 text-right space-x-2">
                    {u.email !== session.email && (
                      <>
                        <button onClick={() => toggleRole(u)} disabled={busy === u.id} className="btn btn-ghost px-2.5 py-1 text-xs">
                          {u.role === "admin" ? "Demote" : "Make admin"}
                        </button>
                        <button
                          onClick={() => toggleActive(u)}
                          disabled={busy === u.id}
                          className={`btn px-2.5 py-1 text-xs ${u.status === "active" ? "btn-danger" : "btn-ghost"}`}
                        >
                          {u.status === "active" ? "Deactivate" : "Reactivate"}
                        </button>
                      </>
                    )}
                  </td>
                </tr>
              ))}
              {users.length === 0 && (
                <tr>
                  <td colSpan={5} className="px-3 py-6 text-center text-gray-500">
                    No accounts yet.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
