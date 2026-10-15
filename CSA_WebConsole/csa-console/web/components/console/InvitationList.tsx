"use client";
import { useEffect, useState } from "react";
import { api, del, post } from "@/lib/api";
import type { IssuedInvitation } from "@/lib/identity-contracts";
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
}
export function InvitationList({
  clientId,
  revision,
  onIssued,
}: {
  clientId: string;
  revision: number;
  onIssued: (token: IssuedInvitation) => void;
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
        <ul className="divide-y divide-ink-100">
          {rows.map((row) => {
            const expired = new Date(row.expires_at).getTime() <= observedAt;
            const status = row.consumed_at
              ? "Accepted"
              : row.revoked_at
                ? "Revoked"
                : expired
                  ? "Expired"
                  : "Pending";
            return (
              <li
                key={row.id}
                className="flex flex-wrap items-center justify-between gap-3 py-4"
              >
                <div className="min-w-0">
                  <p className="break-all text-sm font-medium">{row.email}</p>
                  <p className="mt-1 text-xs text-ink-500">
                    {row.kind === "reset" ? "Password reset" : "Invitation"} ·{" "}
                    {status}
                  </p>
                </div>
                {!row.consumed_at && (
                  <div className="flex flex-wrap gap-2">
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
              </li>
            );
          })}
        </ul>
      )}
    </Panel>
  );
}
