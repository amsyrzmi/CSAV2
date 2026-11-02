"use client";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Pill, Tabs } from "@/components/ui/kit";
import { SWITCH_COPY } from "@/lib/hr-access-copy";
import { hrClient, type HrContext } from "@/lib/hr-api";
import { hrHref } from "@/lib/hr-paths";
import { historyText, overviewRows, PEOPLE_COPY, profileTabs } from "@/lib/hr-people";
import {
  peopleClient, type Department, type Person, type PersonAccess, type PersonHistory, type PersonProfile as Profile,
  type ProfileSection,
} from "@/lib/hr-people-api";
import { isDenied, shortWhen } from "@/lib/hr-pickers";
import { action, secondary, Notice, Panel } from "../ui";
import { PersonForm } from "./PersonForm";
import { MemberSelect, useMemberOptions } from "./Pickers";
import { peopleWriteError } from "./refusal";
import { useHrRunner } from "./useHrRunner";

type Loaded = { profile: Profile } | { missing: true } | { failed: true } | null;

/** A person's basic fields as a definition list. Shared with My HR. */
export function PersonOverview({ person }: { person: Person }) {
  return <dl className="grid gap-x-6 gap-y-3 text-sm sm:grid-cols-[max-content_1fr]">
    {overviewRows(person).map(([label, value]) => <div key={label} className="contents">
      <dt className="text-ink-500">{label}</dt><dd className="font-medium">{value}</dd>
    </div>)}
  </dl>;
}

/**
 * A person's profile: Overview for everyone who may see them, plus Access and History for HR
 * viewers (the HR owner and Manage people holders), as the server lists the sections. Editors
 * get Edit; only the HR owner gets the access page link and account linking.
 */
export function PersonProfile({ tenant, context, readonly, platform, employeeId }: {
  tenant: string; context: HrContext; readonly: boolean; platform: boolean; employeeId: string;
}) {
  const people = useMemo(() => peopleClient(tenant), [tenant]);
  const [loaded, setLoaded] = useState<Loaded>(null);
  const [tab, setTab] = useState<ProfileSection>("overview");
  const [editing, setEditing] = useState<Department[] | null>(null);
  const [opening, setOpening] = useState(false);
  const [message, setMessage] = useState("");
  const [reloadFailed, setReloadFailed] = useState(false);
  const fetchProfile = useCallback(async (): Promise<Loaded> => {
    try { return { profile: await people.person(employeeId) }; }
    catch (cause) { return isDenied(cause) ? { missing: true } : { failed: true }; }
  }, [people, employeeId]);
  useEffect(() => {
    let active = true;
    void fetchProfile().then(result => { if (active) setLoaded(result); });
    return () => { active = false; };
  }, [fetchProfile]);
  const reload = useCallback(async () => {
    const result = await fetchProfile();
    if (result && "profile" in result) { setLoaded(result); setReloadFailed(false); } else setReloadFailed(true);
  }, [fetchProfile]);

  const list = hrHref({ tab: "people", view: "list" }, platform, tenant);
  const back = <Link className="text-sm underline" href={list}>Back to People</Link>;
  if (!loaded) return <Notice>Loading this person…</Notice>;
  if ("missing" in loaded) return <div className="space-y-4">{back}<Notice>{PEOPLE_COPY.profileUnavailable}</Notice></div>;
  if ("failed" in loaded) return <div className="space-y-4">{back}
    <Notice error>We could not load this person. Reload the page and try again.</Notice></div>;
  const { person, sections, can_edit: canEdit } = loaded.profile;
  const tabs = profileTabs(sections);
  const shown = tabs.some(item => item.value === tab) ? tab : "overview";

  async function startEditing() {
    if (opening) return;
    setOpening(true); setMessage("");
    try { setEditing((await people.departments()).items); }
    // Without the list the form still offers the current department, No department and New department….
    catch { setEditing([]); }
    finally { setOpening(false); }
  }

  return <div className="space-y-5">
    {back}
    {message && <Notice>{message}</Notice>}
    {reloadFailed && <Notice error>We could not reload this person. Reload the page and try again.</Notice>}
    {tabs.length > 1 && <Tabs value={shown} onChange={value => { setTab(value); setMessage(""); }} items={tabs} />}
    {shown === "overview" && (editing
      ? <PersonForm key={person.version ?? 0} tenant={tenant} context={context} platform={platform} mode="update"
        person={person} departments={editing} onCancel={() => setEditing(null)} onReload={() => void reload()}
        onSaved={() => { setEditing(null); setMessage("Changes saved."); void reload(); }} />
      : <Panel title={person.display_name}>
        <PersonOverview person={person} />
        {canEdit && !readonly && <button type="button" className={`${action} mt-5`} disabled={opening}
          onClick={() => void startEditing()}>Edit</button>}
      </Panel>)}
    {shown === "access" && <AccessSection tenant={tenant} employeeId={person.employee_id} owner={context.client_owner}
      readonly={readonly} platform={platform} onLinked={() => void reload()} />}
    {shown === "history" && <HistorySection tenant={tenant} employeeId={person.employee_id} version={person.version} />}
  </div>;
}

function AccessSection({ tenant, employeeId, owner, readonly, platform, onLinked }: {
  tenant: string; employeeId: string; owner: boolean; readonly: boolean; platform: boolean; onLinked: () => void;
}) {
  const people = useMemo(() => peopleClient(tenant), [tenant]);
  const [access, setAccess] = useState<PersonAccess | null>(null);
  const [failed, setFailed] = useState(false);
  const load = useCallback(async () => { setAccess(await people.access(employeeId)); }, [people, employeeId]);
  useEffect(() => {
    let active = true;
    people.access(employeeId).then(result => { if (active) setAccess(result); }).catch(() => { if (active) setFailed(true); });
    return () => { active = false; };
  }, [people, employeeId]);
  if (failed) return <Notice error>We could not load this person&apos;s access. Reload the page and try again.</Notice>;
  if (!access) return <Notice>Loading access…</Notice>;
  const tags = [
    ...(access.is_owner ? ["Owner of People & HR"] : []),
    ...access.on.map(id => SWITCH_COPY[id].label),
    ...access.custom,
  ];
  return <Panel title="Access">
    {!access.account ? <p className="text-sm">No console account is linked.{owner ? " Choose a member below to link one." : " Ask the HR owner to link one."}</p>
      : <div className="space-y-3 text-sm">
        <p>Console account: <span className="font-medium">{access.account.display_name ?? "A member"}</span></p>
        {tags.length === 0 ? <p className="text-ink-500">No HR access is switched on for this person.</p>
          : <ul className="flex flex-wrap gap-2" aria-label="Access switched on">
            {tags.map(tag => <li key={tag}><Pill tone="brand" className="normal-case">{tag}</Pill></li>)}</ul>}
      </div>}
    {owner && <div className="mt-5 space-y-5">
      <Link className="text-sm underline" href={hrHref({ tab: "settings" }, platform, tenant)}>Change on the access page</Link>
      {!readonly && <AccountLink tenant={tenant} employeeId={employeeId} linked={access.account?.user_id ?? null}
        onLinked={async () => { await load(); onLinked(); }} />}
    </div>}
  </Panel>;
}

/** HR owner only: link this person to a member's console account, or unlink it. */
function AccountLink({ tenant, employeeId, linked, onLinked }: {
  tenant: string; employeeId: string; linked: string | null; onLinked: () => Promise<void>;
}) {
  const client = useMemo(() => hrClient(tenant), [tenant]);
  const members = useMemberOptions(tenant, "members");
  const { busy, error, message, setMessage, setError, run } = useHrRunner();
  function link(accountId: string | null) {
    void run(async () => {
      try { await client.operation({ operation: "account.link", employee_id: employeeId, account_id: accountId }); }
      catch (cause) { throw new Error(peopleWriteError(cause)); }
      setMessage(accountId ? "Console account linked." : "Console account unlinked.");
      try { await onLinked(); } catch { setError("Saved, but the access could not refresh. Reload the page to see it."); }
    });
  }
  return <form className="space-y-3" aria-busy={busy} onSubmit={event => {
    event.preventDefault();
    const accountId = String(new FormData(event.currentTarget).get("account_id") ?? "").trim();
    if (accountId) link(accountId);
  }}>
    {message && <Notice>{message}</Notice>}
    {error && <Notice error>{error}</Notice>}
    <fieldset disabled={busy} className="flex flex-wrap items-end gap-3">
      <div className="min-w-64"><MemberSelect key={members.loaded ? "loaded" : "loading"} members={members} name="account_id"
        label="Console account" defaultValue={linked ?? ""} /></div>
      <button className={action}>Link account</button>
      {linked && <button type="button" className={secondary} onClick={() => link(null)}>Unlink account</button>}
    </fieldset>
  </form>;
}

function HistorySection({ tenant, employeeId, version }: { tenant: string; employeeId: string; version: number | null }) {
  const people = useMemo(() => peopleClient(tenant), [tenant]);
  const [history, setHistory] = useState<PersonHistory | null>(null);
  const [failed, setFailed] = useState(false);
  // Read again after a save (the version moves), so the change shows up.
  useEffect(() => {
    let active = true;
    people.history(employeeId).then(result => { if (active) setHistory(result); }).catch(() => { if (active) setFailed(true); });
    return () => { active = false; };
  }, [people, employeeId, version]);
  if (failed) return <Notice error>We could not load this person&apos;s history. Reload the page and try again.</Notice>;
  if (!history) return <Notice>Loading history…</Notice>;
  return <Panel title="History">
    {history.items.length === 0 ? <p className="text-sm text-ink-500">No changes yet. Changes to this person will show here.</p>
      : <ol className="space-y-4">{history.items.map((change, index) => <li key={`${change.changed_at}-${index}`}
        className="border-b border-ink-200 pb-3 text-sm">
        <p className="font-medium">{shortWhen(change.changed_at)} · {change.actor_name ?? "A former member"}</p>
        <ul className="mt-1 space-y-1">{change.fields.map(item => <li key={item.field}>{historyText(item)}</li>)}</ul>
      </li>)}</ol>}
    {history.truncated && <p className="mt-4 text-sm text-ink-500">Showing the latest 200 changes.</p>}
  </Panel>;
}
