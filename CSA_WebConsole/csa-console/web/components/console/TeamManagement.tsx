"use client";
import { useEffect, useRef, useState } from "react";
import { api, del, patch, post } from "@/lib/api";
import type {
  Client,
  IssuedInvitation,
  Member,
  TenantRole,
} from "@/lib/identity-contracts";
import { roleLabels } from "@/lib/identity-contracts";
import { action, field, Heading, Notice, Panel, secondary } from "./ui";
import { InvitationList } from "./InvitationList";

const roles: TenantRole[] = ["tenant_admin", "tenant_staff", "read_only"];
export function TeamManagement({
  platform = false,
  clientId = null,
}: {
  platform?: boolean;
  clientId?: string | null;
}) {
  const [clients, setClients] = useState<Client[] | null>(null);
  const [selected, setSelected] = useState(clientId ?? "");
  const selection = useRef(clientId ?? "");
  const [members, setMembers] = useState<Member[] | null>(null);
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [role, setRole] = useState<TenantRole>("tenant_staff");
  const [issued, setIssued] = useState<IssuedInvitation | null>(null);
  const [link, setLink] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    if (!platform) return;
    let live = true;
    api<Client[]>("/console/v1/admin/clients")
      .then((rows) => {
        if (live) setClients(rows);
      })
      .catch((cause) => {
        if (live) setError((cause as Error).message);
      });
    return () => {
      live = false;
    };
  }, [platform]);
  useEffect(() => {
    if (!selected) return;
    let live = true;
    api<Member[]>(`/console/v1/team?client_id=${encodeURIComponent(selected)}`)
      .then((rows) => {
        if (live) setMembers(rows);
      })
      .catch((cause) => {
        if (live) setError((cause as Error).message);
      });
    return () => {
      live = false;
    };
  }, [selected]);
  async function reload() {
    setMembers(
      await api<Member[]>(
        `/console/v1/team?client_id=${encodeURIComponent(selected)}`,
      ),
    );
  }
  function showToken(token: IssuedInvitation) {
    if (selection.current !== selected) return;
    setRevision((value) => value + 1);
    setIssued(token);
    setLink(
      `${window.location.origin}/invite#token=${encodeURIComponent(token.token)}`,
    );
  }
  async function invite(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      showToken(
        await post<IssuedInvitation>("/console/v1/team/invites", {
          client_id: selected,
          email,
          display_name: name,
          role,
        }),
      );
      setEmail("");
      setName("");
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function update(
    member: Member,
    change: { role?: TenantRole; is_enabled?: boolean },
  ) {
    setBusy(true);
    setError("");
    try {
      await patch(
        `/console/v1/team/${member.user_id}?client_id=${encodeURIComponent(selected)}`,
        change,
      );
      await reload();
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function reset(member: Member) {
    setBusy(true);
    setError("");
    try {
      showToken(
        await post<IssuedInvitation>(
          `/console/v1/admin/users/${member.user_id}/reset`,
        ),
      );
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function revokeSessions(member: Member) {
    setBusy(true);
    setError("");
    try {
      await post(`/console/v1/admin/users/${member.user_id}/revoke-sessions`);
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function revokeLink() {
    if (!issued) return;
    setBusy(true);
    setError("");
    try {
      await del(`/console/v1/admin/invites/${issued.id}`);
      setIssued(null);
      setLink("");
      setRevision((value) => value + 1);
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <Heading
        title={platform ? "Tenant members" : "Your team"}
        description="Invite people and manage their access to the selected tenant."
      />
      {error && (
        <div className="mb-5">
          <Notice error>{error}</Notice>
        </div>
      )}
      {platform && (
        <div className="mb-5">
          <label htmlFor="selected-tenant" className="mb-2 block text-sm">
            Selected tenant
          </label>
          <select
            id="selected-tenant"
            className={field}
            value={selected}
            disabled={clients === null || busy}
            onChange={(e) => {
              selection.current = e.target.value;
              setSelected(e.target.value);
              setMembers(null);
              setIssued(null);
              setLink("");
              setError("");
            }}
          >
            <option value="">
              {clients === null ? "Loading tenants…" : "Choose a tenant"}
            </option>
            {clients?.map((client) => (
              <option key={client.id} value={client.id}>
                {client.name} · {client.slug}
              </option>
            ))}
          </select>
        </div>
      )}
      {!selected ? (
        <Notice>Select a tenant to manage its members.</Notice>
      ) : (
        <div className="space-y-5">
          {issued && (
            <Panel title="Share the secure link">
              <p className="mb-3 text-sm text-ink-600">
                Give this single-use link directly to the intended person.
                Expires{" "}
                {new Date(issued.expires_at).toLocaleString(undefined, {
                  timeZone: "Asia/Kuala_Lumpur",
                })}{" "}
                (Malaysia time).
              </p>
              <label htmlFor="issued-link" className="sr-only">
                Secure invitation or reset link
              </label>
              <input
                id="issued-link"
                type="text"
                autoComplete="off"
                readOnly
                className={field}
                value={link}
                onFocus={(e) => e.target.select()}
              />
              <div className="mt-4 flex flex-wrap gap-3">
                <button
                  type="button"
                  className={secondary}
                  onClick={() => {
                    setIssued(null);
                    setLink("");
                  }}
                >
                  Hide link
                </button>
                <button
                  type="button"
                  disabled={busy}
                  className={secondary}
                  onClick={() => void revokeLink()}
                >
                  Revoke link
                </button>
              </div>
            </Panel>
          )}
          <Panel title="Invite a member">
            <form onSubmit={invite} className="grid gap-4 md:grid-cols-2">
              <div>
                <label htmlFor="member-name" className="mb-2 block text-sm">
                  Display name
                </label>
                <input
                  id="member-name"
                  className={field}
                  maxLength={120}
                  required
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                />
              </div>
              <div>
                <label htmlFor="member-email" className="mb-2 block text-sm">
                  Email
                </label>
                <input
                  id="member-email"
                  type="email"
                  className={field}
                  required
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                />
              </div>
              <div>
                <label htmlFor="member-role" className="mb-2 block text-sm">
                  Tenant role
                </label>
                <select
                  id="member-role"
                  className={field}
                  value={role}
                  onChange={(e) => setRole(e.target.value as TenantRole)}
                >
                  {roles.map((value) => (
                    <option key={value} value={value}>
                      {roleLabels[value]}
                    </option>
                  ))}
                </select>
              </div>
              <div className="flex items-end">
                <button className={action} disabled={busy}>
                  {busy ? "Saving…" : "Create invitation"}
                </button>
              </div>
            </form>
          </Panel>
          <Panel title="Members">
            {members === null ? (
              <p className="text-sm text-ink-500">Loading members…</p>
            ) : members.length === 0 ? (
              <p className="text-sm text-ink-500">
                No accepted memberships yet. Create an invitation to get
                started.
              </p>
            ) : (
              <ul className="divide-y divide-ink-100">
                {members.map((member) => (
                  <li
                    key={member.user_id}
                    className="flex flex-wrap items-center justify-between gap-4 py-4"
                  >
                    <div className="min-w-0">
                      <p className="break-words text-sm font-medium">
                        {member.display_name}
                      </p>
                      <p className="break-all text-xs text-ink-500">
                        {member.email}
                      </p>
                      <p className="mt-1 text-xs text-ink-600">
                        {member.is_enabled
                          ? "Membership enabled"
                          : "Membership disabled"}
                      </p>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      <select
                        aria-label={`Role for ${member.email}`}
                        className={field + " w-auto"}
                        disabled={busy}
                        value={member.role}
                        onChange={(e) =>
                          void update(member, {
                            role: e.target.value as TenantRole,
                          })
                        }
                      >
                        {roles.map((value) => (
                          <option key={value} value={value}>
                            {roleLabels[value]}
                          </option>
                        ))}
                      </select>
                      <button
                        type="button"
                        disabled={busy}
                        className={secondary}
                        onClick={() =>
                          void update(member, {
                            is_enabled: !member.is_enabled,
                          })
                        }
                      >
                        {member.is_enabled ? "Disable access" : "Enable access"}
                      </button>
                      {platform && (
                        <>
                          <button
                            type="button"
                            disabled={busy}
                            className={secondary}
                            onClick={() => void reset(member)}
                          >
                            Issue reset
                          </button>
                          <button
                            type="button"
                            disabled={busy}
                            className={secondary}
                            onClick={() => void revokeSessions(member)}
                          >
                            Revoke sessions
                          </button>
                        </>
                      )}
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </Panel>
          <InvitationList
            key={selected}
            clientId={selected}
            revision={revision}
            onIssued={showToken}
          />
        </div>
      )}
    </>
  );
}
