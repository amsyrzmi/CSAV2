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
import SlideOver from "./fs/SlideOver";
import { HrOwnerPanel } from "./HrOwnerPanel";
import { InvitationList } from "./InvitationList";
import MemberCard from "./team/MemberCard";

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
  currentUserId,
}: {
  platform?: boolean;
  clientId?: string | null;
  /** The signed-in user's id, so a change to one's own access says "You will be signed out". */
  currentUserId?: string;
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
  const [inviteOpen, setInviteOpen] = useState(false);
  // One change at a time, even if a second click lands before the page has re-rendered.
  const acting = useRef(false);
  const linkPanel = useRef<HTMLDivElement>(null);
  const inviteButton = useRef<HTMLButtonElement>(null);
  const errorBox = useRef<HTMLDivElement>(null);
  const nameInput = useRef<HTMLInputElement>(null);
  useEffect(() => {
    // A link or message that just appeared may be far above the button that made it.
    if (!shown) return;
    linkPanel.current?.focus({ preventScroll: true });
    linkPanel.current?.scrollIntoView({ block: "nearest" });
  }, [shown]);
  useEffect(() => {
    // The drawer's own effect has already focused its Close button; start on the first question.
    if (inviteOpen) nameInput.current?.focus();
  }, [inviteOpen]);
  useEffect(() => {
    if (error && !inviteOpen) errorBox.current?.scrollIntoView({ block: "nearest" });
  }, [error, inviteOpen]);
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
      setInviteOpen(false);
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setBusy(false);
    }
  }
  /** Sends a member change. Resolves to the server's refusal, shown by the card, or null. */
  async function update(
    member: Member,
    change: { role?: TenantRole; is_enabled?: boolean },
  ): Promise<string | null> {
    if (acting.current) return null;
    acting.current = true;
    setBusy(true);
    setError("");
    try {
      try {
        await patch(
          `/console/v1/team/${member.user_id}?client_id=${encodeURIComponent(selected)}`,
          change,
        );
      } catch (cause) {
        return (cause as Error).message;
      }
      // The change is saved. If only the list cannot be read again, the card's question still
      // closes (it was not refused) and the page says so separately.
      try {
        await reload();
      } catch {
        setError("Saved, but the list could not be refreshed. Reload to see it.");
      }
      return null;
    } finally {
      acting.current = false;
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
  /** Ends every session the person has. Resolves to the server's refusal, or null. */
  async function revokeSessions(member: Member): Promise<string | null> {
    if (acting.current) return null;
    acting.current = true;
    setBusy(true);
    setError("");
    try {
      await post(`/console/v1/admin/users/${member.user_id}/revoke-sessions`);
      return null;
    } catch (cause) {
      return (cause as Error).message;
    } finally {
      acting.current = false;
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
      inviteButton.current?.focus();
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const inviteForm = (
    <form onSubmit={invite} className="grid gap-4">
      <p className="text-sm text-[color:var(--fs-text-secondary)]">
        A link to join this company will be created. You can change their role later.
      </p>
      <div>
        <label htmlFor="member-name" className="mb-2 block text-sm">
          Display name
        </label>
        <input
          id="member-name"
          ref={nameInput}
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
        <div>
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
  );
  return (
    <>
      <Heading
        title={platform ? "Tenant members" : "Your team"}
        description="Invite people and manage their access to the selected tenant."
      />
      {error && !inviteOpen && (
        <div ref={errorBox} className="mb-5">
          <Notice error>{error}</Notice>
        </div>
      )}
      {platform && (
        <div className="mb-5 max-w-3xl">
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
        // One calm column: an opened card pushes the next one down instead of leaving a gap beside it.
        // The bottom padding keeps the last row clear of the floating assistant button on phones.
        <div className="max-w-3xl space-y-5 pb-20 md:pb-0">
          <HrOwnerPanel key={selected} clientId={selected} members={members ?? []} currentUserId={currentUserId} />
          {shown && (
            <div
              ref={linkPanel}
              tabIndex={-1}
              data-testid="team-link-panel"
              className="outline-none"
            >
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
                    onClick={() => {
                      setShown(null);
                      inviteButton.current?.focus();
                    }}
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
            </div>
          )}
          <section aria-labelledby="team-members-title">
            <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
              <h2
                id="team-members-title"
                className="font-(family-name:--fs-font-heading) text-base font-semibold"
              >
                Members
              </h2>
              <button
                ref={inviteButton}
                type="button"
                data-testid="team-invite-open"
                className={action}
                onClick={() => {
                  setError("");
                  setInviteOpen(true);
                }}
              >
                Invite someone
              </button>
            </div>
            {members === null ? (
              <p className="text-sm text-ink-500">Loading members…</p>
            ) : members.length === 0 ? (
              <p className="text-sm text-ink-500">
                No accepted memberships yet. Create an invitation to get
                started.
              </p>
            ) : (
              <ul data-testid="team-members" className="grid gap-3">
                {members.map((member) => (
                  <MemberCard
                    key={member.user_id}
                    member={member}
                    platform={platform}
                    selfId={currentUserId}
                    busy={busy}
                    onChange={update}
                    onReset={(target) => void reset(target)}
                    onRevoke={revokeSessions}
                  />
                ))}
              </ul>
            )}
          </section>
          <InvitationList
            key={selected}
            clientId={selected}
            revision={revision}
            hasHrPackage={facts?.hasHrPackage ?? false}
            onIssued={showToken}
          />
        </div>
      )}
      <SlideOver
        open={inviteOpen}
        onClose={() => {
          setInviteOpen(false);
          setError("");
        }}
        title="Invite a member"
        returnFocusTo={inviteButton}
        widthClass="max-w-lg"
        testId="team-invite-drawer"
        closeTestId="team-invite-close"
      >
        {error && (
          <div className="mb-4">
            <Notice error testId="team-invite-error">
              {error}
            </Notice>
          </div>
        )}
        {inviteForm}
      </SlideOver>
    </>
  );
}
