"use client";
import { useEffect, useState } from "react";
import { api, del, post } from "@/lib/api";
import type { IssuedInvitation } from "@/lib/identity-contracts";
import type { PresetId } from "@/lib/hr-access-api";
import { invitationHrAccess } from "@/lib/invite-access";
import { invitationChip } from "@/lib/team-view";
import StatusLabel from "./fs/StatusLabel";
import { DetailsRow } from "./kit";
import { Notice, Panel, secondary } from "./ui";

interface Invitation {
  id: string;
  email: string;
  display_name: string;
  role: string | null;
  kind: "invite" | "reset";
  expires_at: string;
  consumed_at: string | null;
  revoked_at: string | null;
  hr_preset: PresetId | null;
}
export function InvitationList({
  clientId,
  revision,
  hasHrPackage,
  onIssued,
}: {
  clientId: string;
  revision: number;
  hasHrPackage: boolean;
  onIssued: (
    token: IssuedInvitation,
    email: string,
    kind: "invite" | "reset",
  ) => void;
}) {
  const [rows, setRows] = useState<Invitation[] | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [observedAt] = useState(() => Date.now());
  const endpoint = `/console/v1/admin/invites?client_id=${encodeURIComponent(clientId)}`;
  useEffect(() => {
    let live = true;
    api<Invitation[]>(endpoint)
      .then((result) => {
        if (live) setRows(result);
      })
      .catch((cause) => {
        if (live) setError((cause as Error).message);
      });
    return () => {
      live = false;
    };
  }, [endpoint, revision]);
  async function change(row: Invitation, renew: boolean) {
    setBusy(true);
    setError("");
    try {
      if (renew)
        onIssued(
          await post<IssuedInvitation>(
            `/console/v1/admin/invites/${row.id}/renew`,
          ),
          row.email,
          row.kind,
        );
      else await del(`/console/v1/admin/invites/${row.id}`);
      setRows(await api<Invitation[]>(endpoint));
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Panel title="Invitations and reset links">
      {error && (
        <div className="mb-4">
          <Notice error>{error}</Notice>
        </div>
      )}
      {rows === null ? (
        <p className="text-sm text-ink-500">Loading invitations…</p>
      ) : rows.length === 0 ? (
        <p className="text-sm text-ink-500">No invitations issued.</p>
      ) : (
        <ul data-testid="team-invitations" className="divide-y divide-[color:var(--fs-border)]">
          {rows.map((row) => {
            const expired = new Date(row.expires_at).getTime() <= observedAt;
            const status = row.consumed_at
              ? "Accepted"
              : row.revoked_at
                ? "Revoked"
                : expired
                  ? "Expired"
                  : "Pending";
            const hrAccess =
              row.kind === "invite" ? invitationHrAccess(row.hr_preset, hasHrPackage) : null;
            const chip = invitationChip(status);
            const testId = `team-invitation-${row.id}`;
            return (
              <li key={row.id} className="py-2.5">
                <DetailsRow
                  testId={testId}
                  toggleLabel={row.email}
                  summary={
                    <div className="min-w-0">
                      <p className="break-all text-sm font-medium">{row.email}</p>
                      <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1">
                        <span data-testid={`${testId}-chip`}>
                          <StatusLabel tone={chip.tone}>{chip.label}</StatusLabel>
                        </span>
                        <span className="text-xs text-[color:var(--fs-text-secondary)]">
                          {row.display_name} · {row.kind === "reset" ? "Password reset" : "Invitation"}
                        </span>
                      </div>
                    </div>
                  }
                >
                  <p className="text-xs text-ink-500">
                    {row.kind === "reset" ? "Password reset" : "Invitation"} · {status}
                  </p>
                  {hrAccess && <p className="mt-1 text-xs text-ink-500">{hrAccess}</p>}
                  {!row.consumed_at && (
                    <div className="mt-3 flex flex-wrap justify-start gap-2">
                      {row.kind === "invite" && (
                        <button
                          type="button"
                          disabled={busy}
                          className={secondary}
                          onClick={() => void change(row, true)}
                        >
                          Renew invitation
                        </button>
                      )}
                      {!row.revoked_at && (
                        <button
                          type="button"
                          disabled={busy}
                          className={secondary}
                          onClick={() => void change(row, false)}
                        >
                          Revoke link
                        </button>
                      )}
                    </div>
                  )}
                </DetailsRow>
              </li>
            );
          })}
        </ul>
      )}
    </Panel>
  );
}
