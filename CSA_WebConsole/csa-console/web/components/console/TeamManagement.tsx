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
import { hrClient } from "@/lib/hr-api";
import {
  defaultHrAccess,
  deliveryMessage,
  hrAccessOptions,
  inviteLink,
  type HrAccessValue,
} from "@/lib/invite-access";
import { servicesClient } from "@/lib/services-api";
import { formatDateTime } from "@/lib/subscriptions-api";
import { action, field, Heading, Notice, Panel, secondary } from "./ui";
import { InvitationList } from "./InvitationList";

const roles: TenantRole[] = ["tenant_admin", "tenant_staff", "read_only"];
const MALAYSIA = "Asia/Kuala_Lumpur";
/** What the invite form needs to know about the selected tenant. */
interface Facts {
  hasHrPackage: boolean;
  isHrOwner: boolean;
  timeZone: string;
}
/** The link or message on show, with who it is for. Reset links expire in Malaysia time. */
interface Shown {
  issued: IssuedInvitation;
  email: string;
  kind: "invite" | "reset";
  timeZone: string;
}
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
  const [hrAccess, setHrAccess] = useState<HrAccessValue>(defaultHrAccess("tenant_staff"));
  const [facts, setFacts] = useState<Facts | null>(null);
  const [shown, setShown] = useState<Shown | null>(null);
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
  useEffect(() => {
    if (!selected) return;
    let live = true;
    // The package list and the HR owner decide what the HR access select offers. If either
    // cannot be read, the select stays hidden and the server still refuses a preset it should not give.
    const packages = servicesClient(platform ? selected : null)
      .active()
      .then((rows) => rows.some((row) => row.key === "hr" && row.status === "active"))
      .catch(() => false);
    // Only a tenant member can be the HR owner, and only where HR is switched on.
    const owner = packages.then((hasHrPackage) =>
      platform || !hasHrPackage
        ? false
        : hrClient(selected)
            .context()
            .then((context) => context.client_owner)
            .catch(() => false),
    );
    const zone = platform
      ? Promise.resolve(clients?.find((client) => client.id === selected)?.timezone)
      : api<Client>("/console/v1/profile")
          .then((client) => client.timezone)
          .catch(() => undefined);
    void Promise.all([packages, owner, zone]).then(
      ([hasHrPackage, isHrOwner, timeZone]) => {
        if (live) setFacts({ hasHrPackage, isHrOwner, timeZone: timeZone ?? MALAYSIA });
      },
    );
    return () => {
      live = false;
    };
  }, [platform, selected, clients]);
  const options = facts ? hrAccessOptions({ ...facts, role }) : [];
  async function reload() {
    setMembers(
      await api<Member[]>(
        `/console/v1/team?client_id=${encodeURIComponent(selected)}`,
      ),
    );
  }
  function showToken(
    token: IssuedInvitation,
    to: string,
    kind: "invite" | "reset",
  ) {
    if (selection.current !== selected) return;
    setRevision((value) => value + 1);
    setShown({
      issued: token,
      email: to,
      kind,
      timeZone: kind === "reset" ? MALAYSIA : (facts?.timeZone ?? MALAYSIA),
    });
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
          // Left out when the select is hidden or "No HR access" is chosen.
          ...(options.some((option) => option.value === hrAccess) &&
          hrAccess !== "none"
            ? { hr_preset: hrAccess }
            : {}),
        }),
        email,
        "invite",
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
        member.email,
        "reset",
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
    if (!shown) return;
    setBusy(true);
    setError("");
    try {
      await del(`/console/v1/admin/invites/${shown.issued.id}`);
      setShown(null);
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
              setShown(null);
              setFacts(null);
              setHrAccess(defaultHrAccess(role));
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
          {shown && (
            <Panel
              title={
                shown.issued.delivery === "email"
                  ? "Link emailed"
                  : "Share the secure link"
              }
            >
              <p data-testid="invite-delivery" className="mb-3 text-sm text-ink-600">
                {deliveryMessage(shown.issued, shown.email, shown.kind)}
              </p>
              <p className="mb-3 text-sm text-ink-600">
                {shown.issued.delivery === "email"
                  ? "The link works once."
                  : "Give this single-use link directly to the intended person."}{" "}
                Expires{" "}
                {formatDateTime(shown.issued.expires_at, shown.timeZone)} (
                {shown.timeZone === MALAYSIA
                  ? "Malaysia time"
                  : `${shown.timeZone.replaceAll("_", " ")} time`}
                ).
              </p>
              {shown.issued.delivery === "manual" && (
                <>
                  <label htmlFor="issued-link" className="sr-only">
                    Secure invitation or reset link
                  </label>
                  <input
                    id="issued-link"
                    type="text"
                    autoComplete="off"
                    readOnly
                    className={field}
                    value={inviteLink(shown.issued, window.location.origin)}
                    onFocus={(e) => e.target.select()}
                  />
                </>
              )}
              <div className="mt-4 flex flex-wrap gap-3">
                <button
                  type="button"
                  className={secondary}
                  onClick={() => setShown(null)}
                >
                  {shown.issued.delivery === "email" ? "Hide message" : "Hide link"}
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
                  onChange={(e) => {
                    const next = e.target.value as TenantRole;
                    setRole(next);
                    setHrAccess(defaultHrAccess(next));
                  }}
                >
                  {roles.map((value) => (
                    <option key={value} value={value}>
                      {roleLabels[value]}
                    </option>
                  ))}
                </select>
              </div>
              {options.length > 0 && (
                <div className="md:col-span-2">
                  <label htmlFor="member-hr-access" className="mb-2 block text-sm">
                    HR access
                  </label>
                  <select
                    id="member-hr-access"
                    data-testid="invite-hr-access"
                    className={field}
                    value={hrAccess}
                    onChange={(e) => setHrAccess(e.target.value as HrAccessValue)}
                  >
                    {options.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                  <p className="mt-2 text-xs text-ink-600">
                    {options.find((option) => option.value === hrAccess)?.explanation}
                    {facts?.isHrOwner
                      ? ""
                      : " Only the HR owner can give more than Staff."}
                  </p>
                </div>
              )}
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
