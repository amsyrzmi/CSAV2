"use client";
import { useCallback, useEffect, useMemo, useState } from "react";
import { hrClient, type AgentSwitch } from "@/lib/hr-api";
import {
  accessRecent, applyInvitation, dismissInvitation, pendingInvitations,
  type AccessEvent, type PendingInvitationAccess,
} from "@/lib/hr-access-api";
import { eventText, FEATURE_LABELS, FEATURES_COPY, PENDING_COPY, pendingText } from "@/lib/hr-access-copy";
import { formatDateTime } from "@/lib/subscriptions-api";
import { action, Notice, Panel, secondary } from "./ui";
import { plain } from "./HrAccessPanel";

/**
 * The HR assistant's company features, on the existing agent switch read and `PUT /agents`, one
 * call per changed feature. They never gate a staff switch and no warning depends on them.
 */
export function HrAccessFeatures({ tenant, readonly }: { tenant: string; readonly: boolean }) {
  const client = useMemo(() => hrClient(tenant), [tenant]);
  const [features, setFeatures] = useState<AgentSwitch[] | null>(null);
  const [saving, setSaving] = useState("");
  const [problem, setProblem] = useState("");
  useEffect(() => {
    let active = true;
    client.agents().then(
      (items) => { if (active) setFeatures(items); },
      (cause) => { if (active) setProblem(plain(cause, "We could not load the HR assistant's features. Refresh the page to try again.")); },
    );
    return () => { active = false; };
  }, [client]);
  async function flip(setting: AgentSwitch, enabled: boolean) {
    setSaving(setting.name); setProblem("");
    try {
      const saved = await client.switch({ ...setting, enabled });
      setFeatures((old) => old?.map((item) => item.name === saved.name ? saved : item) ?? old);
    } catch (cause) {
      setProblem(plain(cause, "We could not change that feature. Try again in a minute."));
    } finally { setSaving(""); }
  }
  return <Panel title={FEATURES_COPY.title}>
    <p className="mb-4 text-sm text-ink-600">{FEATURES_COPY.description}</p>
    {problem && <Notice error>{problem}</Notice>}
    {features === null && !problem && <p className="text-sm text-ink-500">Loading the HR assistant&apos;s features…</p>}
    <div className="grid gap-2 sm:grid-cols-2">{features?.map((setting) =>
      <label key={setting.name} className="flex items-center gap-3 rounded-md border border-ink-200 p-3 text-sm">
        <input type="checkbox" className="h-4 w-4" checked={setting.enabled} disabled={readonly || saving !== ""}
          data-testid={`hr-access-feature-${setting.name}`} onChange={(event) => void flip(setting, event.target.checked)} />
        <span>{FEATURE_LABELS[setting.name] ?? setting.name}</span>
      </label>)}</div>
  </Panel>;
}

/** Who changed whose access, newest first. Loads when first opened. */
export function HrAccessRecent({ tenant, refresh }: { tenant: string; refresh: number }) {
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<AccessEvent[] | null>(null);
  const [next, setNext] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [problem, setProblem] = useState("");
  async function load(cursor?: string) {
    setLoading(true); setProblem("");
    try {
      const page = await accessRecent(tenant, cursor);
      setItems((old) => cursor && old ? [...old, ...page.items] : page.items);
      setNext(page.next_cursor);
    } catch (cause) {
      setProblem(plain(cause, "We could not load recent changes. Try again in a minute."));
    } finally { setLoading(false); }
  }
  useEffect(() => {
    if (!open) return;
    let active = true;
    accessRecent(tenant).then(
      (page) => { if (active) { setItems(page.items); setNext(page.next_cursor); } },
      (cause) => { if (active) setProblem(plain(cause, "We could not load recent changes. Try again in a minute.")); },
    );
    return () => { active = false; };
  }, [open, tenant, refresh]);
  return <details className="card p-5 sm:p-6" data-testid="hr-access-recent"
    onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary className="cursor-pointer text-base font-semibold">Recent changes</summary>
    <div className="mt-4 space-y-3">
      {problem && <Notice error>{problem}</Notice>}
      {items === null && !problem && <p className="text-sm text-ink-500">Loading recent changes…</p>}
      {items?.length === 0 && <p className="text-sm text-ink-500">Access changes will appear here after you save them.</p>}
      <ul className="space-y-2">{items?.map((item, index) =>
        <li key={`${item.occurred_at}:${index}`} className="border-b border-ink-200 pb-2 text-sm">
          <span>{eventText(item)}</span>
          <span className="block text-xs text-ink-500">{formatDateTime(item.occurred_at, "Asia/Kuala_Lumpur")}</span>
        </li>)}</ul>
      {next && <button type="button" className={secondary} disabled={loading} onClick={() => void load(next)}>Load more</button>}
    </div>
  </details>;
}

/**
 * Access chosen on an invitation that was not applied when it was accepted: the person who chose
 * a preset other than Staff is no longer the HR owner, or the apply failed (a Staff one too).
 * The owner applies it or dismisses it. Apply answers `saved`; a refusal is an error (409 when it
 * was already handled, 404 when the invitation is unavailable), shown from the catch.
 * Nothing shows while the list is empty.
 */
export function HrAccessPending({ tenant, readonly, onApplied }: { tenant: string; readonly: boolean; onApplied: () => void }) {
  const [items, setItems] = useState<PendingInvitationAccess[] | null>(null);
  const [working, setWorking] = useState("");
  const [problem, setProblem] = useState("");
  const [note, setNote] = useState("");
  const reload = useCallback(async () => {
    try { setItems((await pendingInvitations(tenant)).items); }
    catch (cause) { setProblem(plain(cause, "We could not load the invitations waiting for HR access. Refresh the page to try again.")); }
  }, [tenant]);
  useEffect(() => {
    let active = true;
    pendingInvitations(tenant).then(
      (page) => { if (active) setItems(page.items); },
      (cause) => { if (active) setProblem(plain(cause, "We could not load the invitations waiting for HR access. Refresh the page to try again.")); },
    );
    return () => { active = false; };
  }, [tenant]);
  async function decide(item: PendingInvitationAccess, apply: boolean) {
    setWorking(item.token_id); setProblem(""); setNote("");
    try {
      if (apply) {
        await applyInvitation(tenant, item.token_id);
        setNote(`${item.display_name} now has the access chosen on their invitation.`);
        onApplied();
      } else {
        await dismissInvitation(tenant, item.token_id);
        setNote(`Dismissed. ${item.display_name}'s access stays as it is.`);
      }
      await reload();
    } catch (cause) {
      setProblem(plain(cause, "We could not do that. Reload to see the latest, then try again."));
      await reload();
    } finally { setWorking(""); }
  }
  if (!problem && !note && !items?.length) return null;
  return <div data-testid="hr-access-pending"><Panel title={PENDING_COPY.title}>
    <p className="mb-4 text-sm text-ink-600">{PENDING_COPY.description}</p>
    {problem && <Notice error>{problem}</Notice>}
    {note && <Notice>{note}</Notice>}
    <ul className="divide-y divide-ink-200">{items?.map((item) =>
      <li key={item.token_id} className="flex flex-wrap items-center justify-between gap-3 py-3 text-sm">
        <div className="min-w-0">
          <p className="font-medium">{item.display_name}</p>
          <p className="text-ink-600">{pendingText(item)}</p>
          <p className="text-xs text-ink-500">Accepted {formatDateTime(item.accepted_at, "Asia/Kuala_Lumpur")}</p>
        </div>
        <div className="flex gap-2">
          <button type="button" className={action} disabled={readonly || working !== ""}
            data-testid={`hr-access-pending-apply-${item.token_id}`} onClick={() => void decide(item, true)}>Apply access</button>
          <button type="button" className={secondary} disabled={readonly || working !== ""}
            data-testid={`hr-access-pending-dismiss-${item.token_id}`} onClick={() => void decide(item, false)}>Dismiss</button>
        </div>
      </li>)}</ul>
  </Panel></div>;
}
