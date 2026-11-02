"use client";
import { useEffect, useState } from "react";
import { hrClient } from "@/lib/hr-api";
import { peopleClient } from "@/lib/hr-people-api";
import { isDenied, loadPeopleOptions, memberOptions, type Option } from "@/lib/hr-pickers";
import { field } from "../ui";

/**
 * `denied`: the read refused this viewer (404). `failed`: it could not be read for another reason.
 * Either way the pickers fall back to the typed reference the tools used before, so nothing is blocked.
 */
export interface Options { options: Option[]; loaded: boolean; denied: boolean; failed: boolean }
const start: Options = { options: [], loaded: false, denied: false, failed: false };

/** The people the viewer may see, from the People read, as "Name · Title". */
export function usePeopleOptions(tenant: string): Options {
  const [state, setState] = useState(start);
  useEffect(() => {
    let active = true;
    const people = peopleClient(tenant);
    loadPeopleOptions(cursor => people.page({ cursor }))
      .then(result => { if (active) setState({ ...result, loaded: true, failed: false }); })
      .catch(() => { if (active) setState({ options: [], loaded: true, denied: false, failed: true }); });
    return () => { active = false; };
  }, [tenant]);
  return state;
}

/** Console members: all of them (`members`, HR owner) or those who may run interviews (`interviewers`). */
export function useMemberOptions(tenant: string, area: "members" | "interviewers"): Options {
  const [state, setState] = useState(start);
  useEffect(() => {
    let active = true;
    hrClient(tenant).records<{ user_id: string; display_name: string | null }>(area)
      .then(result => { if (active) setState({ options: memberOptions(result.items), loaded: true, denied: false, failed: false }); })
      .catch(cause => { if (active) setState({ options: [], loaded: true, denied: isDenied(cause), failed: !isDenied(cause) }); });
    return () => { active = false; };
  }, [tenant, area]);
  return state;
}

function Picker({ source, name, label, noneLabel, required, defaultValue }: {
  source: Options; name: string; label: string; noneLabel: string; required: boolean; defaultValue: string;
}) {
  if (source.denied || source.failed)
    return <label className="block text-sm">{label} reference<input className={`${field} mt-1`} name={name}
      maxLength={200} required={required} defaultValue={defaultValue} /></label>;
  return <label className="block text-sm">{label}<select className={`${field} mt-1`} name={name} required={required}
    defaultValue={defaultValue}><option value="">{source.loaded ? noneLabel : "Loading…"}</option>
    {source.options.map(([id, text]) => <option key={id} value={id}>{text}</option>)}</select></label>;
}

type PickerProps = { name: string; label: string; noneLabel?: string; required?: boolean; defaultValue?: string };

export function PersonSelect({ people, noneLabel = "Choose a person…", required = true, defaultValue = "", ...rest }: PickerProps & { people: Options }) {
  return <Picker source={people} noneLabel={noneLabel} required={required} defaultValue={defaultValue} {...rest} />;
}

export function MemberSelect({ members, noneLabel = "Choose a member…", required = true, defaultValue = "", ...rest }: PickerProps & { members: Options }) {
  return <Picker source={members} noneLabel={noneLabel} required={required} defaultValue={defaultValue} {...rest} />;
}
