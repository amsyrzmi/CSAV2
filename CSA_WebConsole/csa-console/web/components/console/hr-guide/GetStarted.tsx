"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { hrHref, type HrRoute } from "@/lib/hr-paths";
import { peopleClient } from "@/lib/hr-people-api";
import { isDenied } from "@/lib/hr-pickers";

/**
 * True once the People list answers with no one in it; null while it loads or when it cannot be
 * read (a viewer the list does not serve, or a failed read). Both of those count as "do not show".
 * One existing GET of `hr/people`, read for its `total`.
 */
export function useNoPeopleYet(tenant: string): boolean | null {
  const [none, setNone] = useState<boolean | null>(null);
  useEffect(() => {
    let active = true;
    peopleClient(tenant).page({})
      .then((page) => { if (active) setNone(page.total === 0); })
      .catch((cause) => { if (active) setNone(isDenied(cause) ? false : null); });
    return () => { active = false; };
  }, [tenant]);
  return none;
}

type Step = { key: string; label: string; hint: string; href: string };

const link =
  "inline-flex min-h-10 items-center rounded-[var(--fs-radius-control)] font-medium text-[color:var(--fs-primary)] underline underline-offset-2 hover:bg-[var(--fs-surface-blue)] focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--fs-focus)] pointer-coarse:min-h-11";

/**
 * Where to begin in a company with no people yet. Steps are links; a step the viewer cannot
 * do is left out: adding people needs "Manage people" (or the HR owner), the two Settings steps
 * are for the HR owner, and the Team page is for tenant admins.
 */
export default function GetStarted({ tenant, platform, owner, canAdd, admin }: {
  tenant: string;
  platform: boolean;
  owner: boolean;
  canAdd: boolean;
  admin: boolean;
}) {
  const to = (route: HrRoute) => hrHref(route, platform, tenant);
  const steps: Step[] = [];
  if (canAdd) steps.push({ key: "add", label: "Add your first person", hint: "Start with their name, job title and start date.", href: to({ tab: "people", view: "new" }) });
  if (owner) {
    steps.push({ key: "calendar", label: "Set your work calendar", hint: "Choose the working days and public holidays.", href: to({ tab: "settings" }) });
    steps.push({ key: "access", label: "Choose who can do what", hint: "Give each member the permissions they need.", href: to({ tab: "settings" }) });
  }
  if (admin) steps.push({ key: "invite", label: "Invite your team", hint: "Send invitations from the Team page.", href: "/portal/team" });
  if (steps.length === 0) return null;
  return <section data-testid="hr-get-started" aria-labelledby="hr-get-started-title" className="min-w-0 space-y-3">
    <div className="space-y-1">
      <h3 id="hr-get-started-title" className="text-base font-semibold text-[color:var(--fs-text)]">Get started</h3>
      <p className="max-w-prose text-sm leading-6 text-[color:var(--fs-text-secondary)]">No one has been added yet. These steps set up People &amp; HR.</p>
    </div>
    <ol className="space-y-2">{steps.map((step, index) => <li key={step.key} data-testid={`hr-get-started-${step.key}`}
      className="flex min-w-0 items-start gap-3 rounded-[var(--fs-radius-tile)] border border-[color:var(--fs-border)] bg-[var(--fs-surface)] p-3">
      <span aria-hidden className="grid size-7 shrink-0 place-items-center rounded-full bg-[var(--fs-surface-blue)] text-sm font-semibold text-[color:var(--fs-primary)]">{index + 1}</span>
      <span className="min-w-0 [overflow-wrap:anywhere]">
        <Link className={link} href={step.href}>{step.label}</Link>
        <span className="block text-sm text-[color:var(--fs-text-secondary)]">{step.hint}</span>
      </span>
    </li>)}</ol>
  </section>;
}
