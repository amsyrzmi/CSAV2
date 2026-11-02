"use client";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { hrClient, type HrContext, type Training } from "@/lib/hr-api";
import { hrHref } from "@/lib/hr-paths";
import { emptyText, myBalanceLines, myTrainingLines, PEOPLE_COPY } from "@/lib/hr-people";
import { peopleClient, type Person } from "@/lib/hr-people-api";
import { isDenied } from "@/lib/hr-pickers";
import { action, Notice, Panel } from "../ui";
import { PersonOverview } from "./PersonProfile";

type Balance = { employee_id: string; year: number; leave_type: string; remaining: string; days: string };
/** `null` while loading; `hidden` when the read is not for this viewer (404). */
type Read<T> = { items: T } | { hidden: true } | { failed: true } | null;

function useRead<T>(load: (() => Promise<T>) | null): Read<T> {
  const [state, setState] = useState<Read<T>>(null);
  useEffect(() => {
    if (!load) return;
    let active = true;
    load().then(items => { if (active) setState({ items }); })
      .catch(cause => { if (active) setState(isDenied(cause) ? { hidden: true } : { failed: true }); });
    return () => { active = false; };
  }, [load]);
  return state;
}

/**
 * Home for staff without HR duties: their own profile, leave balances and training requests.
 * Personal details and My documents come with project 1b.
 */
export function MyHr({ tenant, context, readonly, platform }: {
  tenant: string; context: HrContext; readonly: boolean; platform: boolean;
}) {
  const own = context.employee?.employee_id ?? null;
  const people = useMemo(() => peopleClient(tenant), [tenant]);
  const client = useMemo(() => hrClient(tenant), [tenant]);
  const canTrain = context.actions.includes("training.read");
  const profile = useRead<Person>(useMemo(() => own ? async () => (await people.person(own)).person : null, [people, own]));
  const balances = useRead<Balance[]>(useMemo(() => own ? async () => (await client.records<Balance>("balances")).items : null,
    [client, own]));
  const training = useRead<Training[]>(useMemo(() => own && canTrain ? async () => (await client.training()).items : null,
    [client, own, canTrain]));
  const canRequest = !readonly && context.actions.includes("training.draft");

  if (!own) return <Panel title="My profile"><p className="text-sm">{emptyText("unlinked")}</p></Panel>;
  const balanceLines = balances && "items" in balances ? myBalanceLines(balances.items, own) : [];
  const trainingLines = training && "items" in training ? myTrainingLines(training.items, own) : [];
  return <div className="space-y-5">
    <Panel title="My profile">
      {!profile ? <Notice>Loading your profile…</Notice>
        : "items" in profile ? <PersonOverview person={profile.items} />
          : "hidden" in profile ? <p className="text-sm">{PEOPLE_COPY.profileUnavailable}</p>
            : <Notice error>We could not load your profile. Reload the page and try again.</Notice>}
    </Panel>
    {!(balances && "hidden" in balances) && <Panel title="My leave balances">
      {!balances ? <Notice>Loading your leave balances…</Notice>
        : "failed" in balances ? <Notice error>We could not load your leave balances. Reload the page and try again.</Notice>
          : balanceLines.length === 0 ? <p className="text-sm text-ink-500">No leave balances yet. Your HR owner adds them for each year.</p>
            : <ul className="space-y-2 text-sm">{balanceLines.map(line => <li key={line.key}>{line.text}</li>)}</ul>}
    </Panel>}
    {canTrain && !(training && "hidden" in training) && <Panel title="My training requests">
      {canRequest && <Link className={`${action} mb-4`} href={hrHref({ tab: "training" }, platform, tenant)}>Request training</Link>}
      {!training ? <Notice>Loading your training requests…</Notice>
        : "failed" in training ? <Notice error>We could not load your training requests. Reload the page and try again.</Notice>
          : trainingLines.length === 0
            ? <p className="text-sm text-ink-500">No training requests yet.{canRequest && " Choose Request training to ask for one."}</p>
            : <ul className="space-y-2 text-sm">{trainingLines.map(line => <li key={line.key}>
              <span className="font-medium">{line.title}</span> · {line.status}</li>)}</ul>}
    </Panel>}
  </div>;
}
