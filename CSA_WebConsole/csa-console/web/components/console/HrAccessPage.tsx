"use client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError } from "@/lib/api";
import { hrClient } from "@/lib/hr-api";
import {
  accessPeople, applyPreset, changeCount, isAccessChanged, PAGE_SIZE, rebasePending, saveAccessChanges, setSwitch,
  SWITCH_IDS, type AccessChange, type AccessChangeResult, type AccessPeoplePage, type AccessPerson,
  type AccessPersonResult, type AccessWarningOut, type Pending, type PresetId, type SwitchId,
} from "@/lib/hr-access-api";
import {
  changesSummary, PAGE_COPY, PRESET_COPY, refusalText, SWITCH_COPY, warningText,
} from "@/lib/hr-access-copy";
import { HrAccessFeatures, HrAccessRecent } from "./HrAccessMore";
import { HrAccessPanel, initials, plain, warningsFor } from "./HrAccessPanel";
import { action, field, Notice, secondary } from "./ui";

type Filters = { q: string; team: string; has: SwitchId | ""; reports_to: string };
type Employee = { employee_id: string; display_name: string; manager_id: string | null; department: string | null; active: boolean };
const NO_FILTERS: Filters = { q: "", team: "", has: "", reports_to: "" };
const warningKey = (w: AccessWarningOut) => `${w.kind}:${w.user_id ?? ""}:${w.employee_id ?? ""}`;
const RESULT_TEXT: Record<AccessPersonResult["outcome"], string> = { saved: "Saved", unchanged: "No change needed", refused: "Not saved" };

/**
 * "Who can do what": the HR owner's people list (left) and the selected member's switches (right).
 * Pending changes are each member's full desired switch set; the server works out the rows, the
 * warnings and the version, and refuses a save made against a stale page.
 */
export function HrAccessPage({ tenant, readonly, onAdvanced }: { tenant: string; readonly: boolean; onAdvanced: () => void }) {
  const [filters, setFilters] = useState<Filters>(NO_FILTERS);
  const [search, setSearch] = useState("");
  const [cursors, setCursors] = useState<string[]>([]);
  const cursor = cursors.at(-1);
  const [page, setPage] = useState<AccessPeoplePage | null>(null);
  const [version, setVersion] = useState("");
  const [pending, setPendingState] = useState<Pending>({});
  const pendingRef = useRef<Pending>({});
  const batch = useRef("");
  const [results, setResults] = useState<Record<string, AccessPersonResult>>({});
  const [ticks, setTicks] = useState<string[]>([]);
  const [selected, setSelected] = useState<AccessPerson | null>(null);
  const [screen, setScreen] = useState<"list" | "person">("list");
  const [review, setReview] = useState<{ changes: AccessChange[]; result: AccessChangeResult; problem: string } | null>(null);
  const [conflict, setConflict] = useState(false);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState("");
  const [notice, setNotice] = useState("");
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [bulkPreset, setBulkPreset] = useState<PresetId>("staff");
  const [bulkSwitch, setBulkSwitch] = useState<SwitchId>("directory");
  const [recentRefresh, setRecentRefresh] = useState(0);
  const sequence = useRef(0);
  const seen = useRef("");

  /** Any change to the pending set gets a new batch id; a retry of the same set reuses it. */
  const setPending = useCallback((next: Pending) => {
    pendingRef.current = next; setPendingState(next); batch.current = crypto.randomUUID();
  }, []);

  /**
   * A rebase (Reload, or after a save) takes the latest version and moves the pending changes of
   * everyone on this page onto their latest switches. Resolves true when the list loaded.
   */
  const load = useCallback(async (where: { filters: Filters; cursor?: string }, rebase: boolean): Promise<boolean> => {
    const mine = ++sequence.current;
    try {
      const found = await accessPeople(tenant, { ...where.filters, cursor: where.cursor });
      if (mine !== sequence.current) return false;
      setPage(found); setProblem("");
      const held = pendingRef.current;
      if (rebase || Object.keys(held).length === 0) setVersion(found.version);
      if (rebase && Object.keys(held).length > 0) setPending(rebasePending(held, found.items));
      // Once access has moved on, the panel never keeps editing a member from an older load.
      const advanced = seen.current !== "" && seen.current !== found.version;
      seen.current = found.version;
      setSelected((old) => {
        const fresh = found.items.find((p) => p.user_id === old?.user_id);
        if (fresh) return fresh;
        if (old && advanced) return null;
        return old ?? found.items[0] ?? null;
      });
      return true;
    } catch (cause) {
      if (mine !== sequence.current) return false;
      if (cause instanceof ApiError && cause.code === "invalid_cursor") { setCursors([]); return false; }
      setProblem(plain(cause, "We could not load your members' access. Refresh the page to try again."));
      return false;
    }
  }, [tenant, setPending]);

  useEffect(() => { void load({ filters, cursor }, false); }, [load, filters, cursor]);
  useEffect(() => {
    const timer = window.setTimeout(() => {
      setFilters((old) => old.q === search.trim() ? old : { ...old, q: search.trim() });
      setCursors((old) => old.length ? [] : old);
    }, 300);
    return () => window.clearTimeout(timer);
  }, [search]);
  useEffect(() => {
    let active = true;
    hrClient(tenant).records<Employee>("employees").then(
      (found) => { if (active) setEmployees(found.items.filter((e) => e.active)); },
      () => { /* The Team and Reports to filters stay empty; the list still works. */ },
    );
    return () => { active = false; };
  }, [tenant]);

  const teams = useMemo(() => [...new Set(employees.map((e) => e.department).filter((d): d is string => !!d))].sort(), [employees]);
  const managers = useMemo(() => {
    const ids = new Set(employees.map((e) => e.manager_id).filter(Boolean));
    return employees.filter((e) => ids.has(e.employee_id)).sort((a, b) => a.display_name.localeCompare(b.display_name));
  }, [employees]);

  const items = page?.items ?? [];
  const warnings = page?.warnings ?? [];
  const desiredOf = (person: AccessPerson) => pending[person.user_id]?.desired ?? person.on;
  const baseOf = (person: AccessPerson) => pending[person.user_id]?.base ?? person.on;
  const people = Object.keys(pending).length;
  const changes = Object.values(pending).reduce((sum, entry) => sum + changeCount(entry.base, entry.desired), 0);
  const locked = readonly || busy || review !== null;

  function filter(next: Partial<Filters>) { setFilters((old) => ({ ...old, ...next })); setCursors([]); setTicks([]); }
  function turnPage(next: string[]) { setCursors(next); setTicks([]); }

  /** Records each member's new desired set; a member already back at their saved set leaves the bar. */
  function edit(updates: { person: AccessPerson; desired: SwitchId[] }[]) {
    const held = pendingRef.current;
    const next: Pending = { ...held };
    for (const { person, desired } of updates) {
      const base = held[person.user_id]?.base ?? person.on;
      if (changeCount(base, desired) === 0) { delete next[person.user_id]; continue; }
      next[person.user_id] = { base, desired, person: held[person.user_id]?.person ?? person };
    }
    if (Object.keys(next).length > PAGE_SIZE) { setNotice(PAGE_COPY.tooMany); return; }
    setNotice("");
    setResults((old) => {
      const kept = { ...old };
      for (const { person } of updates) delete kept[person.user_id];
      return kept;
    });
    setPending(next);
  }
  const ticked = items.filter((person) => ticks.includes(person.user_id));

  async function dryRun() {
    const changeList: AccessChange[] = Object.entries(pendingRef.current)
      .map(([user_id, entry]) => ({ user_id, on: entry.desired, base: entry.base }));
    if (!changeList.length || !version) return;
    setBusy(true); setProblem(""); setConflict(false);
    try {
      const result = await saveAccessChanges(tenant, { batch_id: batch.current, version, dry_run: true, changes: changeList });
      setReview({ changes: changeList, result, problem: "" });
    } catch (cause) {
      if (isAccessChanged(cause)) setConflict(true);
      else setProblem(plain(cause, "We could not check these changes. Nothing was saved. Try again in a minute."));
    } finally { setBusy(false); }
  }

  async function confirm() {
    if (!review) return;
    setBusy(true);
    try {
      const result = await saveAccessChanges(tenant, { batch_id: batch.current, version, dry_run: false, changes: review.changes });
      setReview(null);
      const outcomes = Object.fromEntries(result.results.map((item) => [item.user_id, item]));
      setResults((old) => ({ ...old, ...outcomes }));
      // This batch is finished, so whatever stays pending (the refused people) gets a new batch id:
      // reusing this one would make the server treat their next save as a replay.
      const next: Pending = { ...pendingRef.current };
      for (const item of result.results) if (item.outcome !== "refused") delete next[item.user_id];
      setPending(next);
      setVersion(result.version);
      const refused = result.results.filter((item) => item.outcome === "refused").length;
      setNotice(refused ? `${refused} ${refused === 1 ? "change was" : "changes were"} not saved. They stay in the bar below with the reason.` : "Access saved.");
      setRecentRefresh((n) => n + 1);
      await load({ filters, cursor }, true);
    } catch (cause) {
      if (isAccessChanged(cause)) { setReview(null); setConflict(true); }
      else setReview((old) => old && { ...old, problem: plain(cause, "We could not confirm the save. Try again; nothing is saved twice.") });
    } finally { setBusy(false); }
  }

  async function reload() {
    setBusy(true); setProblem("");
    try { if (await load({ filters, cursor }, true)) setConflict(false); } finally { setBusy(false); }
  }

  const fresh = review ? review.result.warnings.filter((w) => !warnings.some((old) => warningKey(old) === warningKey(w))) : [];
  const still = review ? review.result.warnings.filter((w) => warnings.some((old) => warningKey(old) === warningKey(w))) : [];
  const current = selected ? pending[selected.user_id]?.person ?? selected : null;

  return <div className="space-y-5 pb-24">
    <div>
      <h2 className="text-lg font-semibold">{PAGE_COPY.title}</h2>
      <p className="mt-1 text-sm text-ink-600">{PAGE_COPY.description}</p>
    </div>
    {problem && <Notice error>{problem}</Notice>}
    {notice && <Notice>{notice}</Notice>}
    {conflict && <div data-testid="hr-access-conflict" role="alert" className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-amber-300 bg-amber-50 p-3 text-sm">
      <span>{PAGE_COPY.conflict}</span>
      <button type="button" className={secondary} disabled={busy} onClick={() => void reload()}>Reload access</button>
    </div>}
    {warnings.length > 0 && <details className="rounded-md border border-amber-200 bg-amber-50 p-3 text-sm">
      <summary className="cursor-pointer font-medium">{warnings.length} {warnings.length === 1 ? "thing" : "things"} to check across your company</summary>
      <ul className="mt-2 list-disc space-y-1 pl-5">{warnings.map((w) => <li key={warningKey(w)}>{warningText(w)}</li>)}</ul>
    </details>}

    <div className="grid gap-5 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
      <section className={`${screen === "person" ? "hidden" : ""} card space-y-4 p-5 lg:block`} aria-label="Members">
        <label className="block text-sm">Search members
          <input className={`${field} mt-1`} type="search" data-testid="hr-access-search" value={search} maxLength={120}
            placeholder="Type part of a name" onChange={(event) => setSearch(event.target.value)} />
        </label>
        <div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-1 xl:grid-cols-3">
          <label className="block text-sm">Team
            <select className={`${field} mt-1`} value={filters.team} onChange={(event) => filter({ team: event.target.value })}>
              <option value="">All teams</option>{teams.map((team) => <option key={team} value={team}>{team}</option>)}
            </select>
          </label>
          <label className="block text-sm">Reports to
            <select className={`${field} mt-1`} value={filters.reports_to} onChange={(event) => filter({ reports_to: event.target.value })}>
              <option value="">Anyone</option>{managers.map((m) => <option key={m.employee_id} value={m.employee_id}>{m.display_name}</option>)}
            </select>
          </label>
          <label className="block text-sm">Has switch
            <select className={`${field} mt-1`} value={filters.has} onChange={(event) => filter({ has: event.target.value as SwitchId | "" })}>
              <option value="">Any switch</option>
              {SWITCH_IDS.map((id) => <option key={id} value={id}>{SWITCH_COPY[id].label} ({page?.counts[id] ?? 0})</option>)}
            </select>
          </label>
        </div>
        {ticked.length > 0 && <div data-testid="hr-access-bulk" className="space-y-3 rounded-md border border-brand-200 bg-brand-50 p-3 text-sm">
          <p className="font-medium">{ticked.length} {ticked.length === 1 ? "member" : "members"} ticked</p>
          <div className="flex flex-wrap items-end gap-2">
            <label className="block grow">Preset
              <select className={`${field} mt-1`} value={bulkPreset} onChange={(event) => setBulkPreset(event.target.value as PresetId)}>
                {(Object.keys(PRESET_COPY) as PresetId[]).map((id) => <option key={id} value={id}>{PRESET_COPY[id].label}</option>)}
              </select>
            </label>
            <button type="button" className={secondary} disabled={locked}
              onClick={() => edit(ticked.map((person) => ({ person, desired: applyPreset(desiredOf(person), bulkPreset) })))}>Apply preset</button>
          </div>
          <div className="flex flex-wrap items-end gap-2">
            <label className="block grow">Switch
              <select className={`${field} mt-1`} value={bulkSwitch} onChange={(event) => setBulkSwitch(event.target.value as SwitchId)}>
                {SWITCH_IDS.map((id) => <option key={id} value={id}>{SWITCH_COPY[id].label}</option>)}
              </select>
            </label>
            <button type="button" className={secondary} disabled={locked}
              onClick={() => edit(ticked.map((person) => ({ person, desired: setSwitch(desiredOf(person), bulkSwitch, true, baseOf(person)) })))}>Turn on</button>
            <button type="button" className={secondary} disabled={locked}
              onClick={() => edit(ticked.map((person) => ({ person, desired: setSwitch(desiredOf(person), bulkSwitch, false, baseOf(person)) })))}>Turn off</button>
          </div>
          <button type="button" className="text-brand-700 underline" onClick={() => setTicks([])}>Clear ticks</button>
        </div>}
        {page && items.length === 0 && <p className="text-sm text-ink-500">{PAGE_COPY.empty}</p>}
        {!page && !problem && <p className="text-sm text-ink-500">Loading members…</p>}
        <ul className="divide-y divide-ink-200">{items.map((person) => {
          const desired = desiredOf(person);
          const outcome = results[person.user_id];
          const flagged = warningsFor(person, warnings).length > 0;
          const name = person.display_name ?? "Unnamed member";
          return <li key={person.user_id} data-testid={`hr-access-row-${person.user_id}`}
            className={`flex items-start gap-3 py-3 ${selected?.user_id === person.user_id ? "bg-brand-50" : ""}`}>
            <input type="checkbox" className="mt-3 h-4 w-4 shrink-0" aria-label={`Tick ${name}`} data-testid={`hr-access-tick-${person.user_id}`}
              checked={ticks.includes(person.user_id)} disabled={readonly}
              onChange={(event) => setTicks((old) => event.target.checked ? [...old, person.user_id] : old.filter((id) => id !== person.user_id))} />
            <button type="button" className="flex min-w-0 grow items-start gap-3 text-left"
              onClick={() => { setSelected(person); setScreen("person"); }}>
              <span aria-hidden className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-ink-100 text-sm font-semibold text-ink-700">{initials(person.display_name)}</span>
              <span className="min-w-0">
                <span className="block font-medium">{name}</span>
                <span className="mt-1 flex flex-wrap gap-1">
                  {desired.map((id) => <span key={id} className="rounded-full bg-ink-100 px-2 py-0.5 text-xs text-ink-700">{SWITCH_COPY[id].tag}</span>)}
                  {desired.length === 0 && <span className="text-xs text-ink-500">No HR access</span>}
                  {person.custom.length > 0 && <span className="rounded-full bg-amber-100 px-2 py-0.5 text-xs text-amber-800">Custom access</span>}
                  {flagged && <span className="rounded-full bg-amber-100 px-2 py-0.5 text-xs text-amber-800">Warning</span>}
                  {pending[person.user_id] && <span className="rounded-full bg-brand-100 px-2 py-0.5 text-xs text-brand-800">Not saved yet</span>}
                </span>
                {outcome && <span data-testid={`hr-access-result-${person.user_id}`}
                  className={`mt-1 block text-xs font-medium ${outcome.outcome === "refused" ? "text-red-700" : "text-emerald-700"}`}>
                  {RESULT_TEXT[outcome.outcome]}{outcome.outcome === "refused" ? `. ${refusalText(outcome.reason)}` : ""}
                </span>}
              </span>
            </button>
          </li>;
        })}</ul>
        {(cursors.length > 0 || page?.next_cursor) && <div className="flex flex-wrap gap-2">
          <button type="button" className={secondary} disabled={cursors.length === 0} onClick={() => turnPage(cursors.slice(0, -1))}>Show previous page</button>
          <button type="button" className={secondary} disabled={!page?.next_cursor}
            onClick={() => page?.next_cursor && turnPage([...cursors, page.next_cursor])}>Show next page</button>
        </div>}
      </section>

      <div className={`${screen === "list" ? "hidden" : ""} lg:block`}>
        {current ? <HrAccessPanel key={current.user_id} person={current} desired={desiredOf(current)}
          others={items.filter((p) => p.user_id !== current.user_id)} warnings={warningsFor(current, warnings)}
          result={results[current.user_id]} disabled={locked} onBack={() => setScreen("list")} onAdvanced={onAdvanced}
          onChange={(desired) => edit([{ person: current, desired }])} />
          : <section className="card p-5 text-sm text-ink-500">{PAGE_COPY.choose}</section>}
      </div>
    </div>

    <HrAccessFeatures tenant={tenant} readonly={readonly} />
    <HrAccessRecent tenant={tenant} refresh={recentRefresh} />

    {people > 0 && <div className="sticky bottom-0 z-10 flex flex-wrap items-center justify-between gap-3 rounded-md border border-ink-200 bg-white p-3 shadow-lg">
      <span className="text-sm font-medium">{changesSummary(changes, people)}</span>
      <div className="flex gap-2">
        <button type="button" className={secondary} disabled={busy || review !== null} onClick={() => { setPending({}); setNotice(""); }}>Discard changes</button>
        <button type="button" className={action} data-testid="hr-access-save" disabled={locked || !version} onClick={() => void dryRun()}>Save changes</button>
      </div>
    </div>}

    {review && <div className="fixed inset-0 z-40 flex items-end justify-center bg-ink-900/40 p-4 sm:items-center">
      <div role="dialog" aria-modal="true" aria-labelledby="hr-access-review-title" className="card max-h-[85vh] w-full max-w-lg space-y-4 overflow-y-auto p-5 sm:p-6">
        <h2 id="hr-access-review-title" className="text-base font-semibold">Save {changesSummary(changes, people)}?</h2>
        {review.problem && <Notice error>{review.problem}</Notice>}
        {review.result.results.filter((item) => item.outcome !== "saved").length > 0 && <ul className="list-disc space-y-1 pl-5 text-sm">
          {review.result.results.filter((item) => item.outcome !== "saved").map((item) => <li key={item.user_id}>
            {pendingRef.current[item.user_id]?.person.display_name ?? "A member"}: {item.outcome === "refused" ? `Won't save. ${refusalText(item.reason)}` : "Nothing to change."}
          </li>)}
        </ul>}
        {fresh.length > 0 ? <div className="space-y-2 rounded-md border border-amber-200 bg-amber-50 p-3 text-sm">
          <h3 className="font-semibold">After this change</h3>
          <ul className="list-disc space-y-1 pl-5">{fresh.map((w) => <li key={warningKey(w)}>{warningText(w)}</li>)}</ul>
          <p>You can still save. These are warnings, not errors.</p>
        </div> : <p className="text-sm">This change adds no new warnings.</p>}
        {still.length > 0 && <details className="text-sm"><summary className="cursor-pointer">{still.length} earlier {still.length === 1 ? "warning stays" : "warnings stay"}</summary>
          <ul className="mt-2 list-disc space-y-1 pl-5">{still.map((w) => <li key={warningKey(w)}>{warningText(w)}</li>)}</ul></details>}
        <div className="flex flex-wrap justify-end gap-2">
          <button type="button" className={secondary} disabled={busy} onClick={() => setReview(null)}>Go back</button>
          <button type="button" className={action} data-testid="hr-access-confirm" disabled={busy} onClick={() => void confirm()}>Save changes</button>
        </div>
      </div>
    </div>}
  </div>;
}
