"use client";
import { useCallback, useEffect, useMemo, useState } from "react";
import { hrClient } from "@/lib/hr-api";
import { departmentBody, emptyText } from "@/lib/hr-people";
import { peopleClient, type Department } from "@/lib/hr-people-api";
import { action, field, secondary, Notice, Panel } from "../ui";
import { peopleWriteError } from "./refusal";
import { useHrRunner } from "./useHrRunner";

/** The department being added or renamed. A new department's id is made when its form opens and kept for retries. */
type Editing = { departmentId: string; name: string; version: number | null; archived: boolean };

/**
 * Departments (HR owner and Manage people): the list with its people counts, add, rename, archive
 * and unarchive. Typing a name that exists in other case or spacing reuses that department on the
 * server, so nothing here folds names.
 */
export function Departments({ tenant, readonly }: { tenant: string; readonly: boolean }) {
  const reads = useMemo(() => peopleClient(tenant), [tenant]);
  const writes = useMemo(() => hrClient(tenant), [tenant]);
  const { busy, error, message, setMessage, setError, run } = useHrRunner();
  const [items, setItems] = useState<Department[] | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  const [editing, setEditing] = useState<Editing | null>(null);
  const load = useCallback(async () => { setItems((await reads.departments()).items); }, [reads]);
  useEffect(() => {
    let active = true;
    reads.departments()
      .then(result => { if (active) setItems(result.items); })
      .catch(() => { if (active) setLoadFailed(true); });
    return () => { active = false; };
  }, [reads]);

  function save(input: Editing, done: string, afterSave?: () => void) {
    void run(async () => {
      try { await writes.operation(departmentBody({ ...input })); }
      catch (cause) { throw new Error(peopleWriteError(cause)); }
      afterSave?.(); setMessage(done);
      try { await load(); } catch { setError("Saved, but the list could not refresh. Reload the page to see it."); }
    });
  }
  const writable = !readonly;
  if (loadFailed) return <Notice error>We could not load the departments. Reload the page and try again.</Notice>;
  if (!items) return <Notice>Loading departments…</Notice>;
  return <div className="space-y-5" aria-busy={busy}>
    {message && <Notice>{message}</Notice>}
    {error && <Notice error>{error}</Notice>}
    <Panel title="Departments">
      {writable && !(editing && editing.version === null) && <button type="button" className={`${action} mb-4`} disabled={busy}
        onClick={() => setEditing({ departmentId: crypto.randomUUID(), name: "", version: null, archived: false })}>Add department</button>}
      {editing && editing.version === null && <NameForm key={editing.departmentId} label="Department name" submit="Save department"
        initial="" busy={busy} onCancel={() => setEditing(null)}
        onSave={name => save({ ...editing, name }, "Department added.", () => setEditing(null))} />}
      {items.length === 0 ? <p className="text-sm text-ink-500">{emptyText("departments", writable)}</p>
        : <div className="overflow-x-auto"><table className="w-full text-left text-sm">
          <thead><tr>{["Name", "People", "Status", ...(writable ? ["Actions"] : [])].map(label => <th className="p-2" key={label} scope="col">{label}</th>)}</tr></thead>
          <tbody>{items.map(item => <tr key={item.department_id} className="border-t border-ink-200">
            {editing?.departmentId === item.department_id
              ? <td className="p-2" colSpan={writable ? 4 : 3}><NameForm label={`New name for ${item.name}`} submit="Save name" initial={item.name}
                busy={busy} onCancel={() => setEditing(null)}
                onSave={name => save({ ...editing, name }, "Department renamed.", () => setEditing(null))} /></td>
              : <>
                <td className="p-2 font-medium">{item.name}</td>
                <td className="p-2">{item.people}</td>
                <td className="p-2">{item.archived ? "Archived" : "In use"}</td>
                {writable && <td className="p-2"><div className="flex flex-wrap gap-2">
                  <button type="button" className={secondary} disabled={busy}
                    onClick={() => setEditing({ departmentId: item.department_id, name: item.name, version: item.version, archived: item.archived })}>
                    Rename {item.name}</button>
                  <button type="button" className={secondary} disabled={busy}
                    onClick={() => save({ departmentId: item.department_id, name: item.name, version: item.version, archived: !item.archived },
                      item.archived ? "Department restored." : "Department archived.")}>
                    {item.archived ? "Unarchive" : "Archive"} {item.name}</button>
                </div></td>}
              </>}
          </tr>)}</tbody>
        </table></div>}
      <p className="mt-4 text-sm text-ink-500">An archived department keeps its people until you move them.</p>
    </Panel>
  </div>;
}

function NameForm({ label, submit, initial, busy, onSave, onCancel }: {
  label: string; submit: string; initial: string; busy: boolean; onSave: (name: string) => void; onCancel: () => void;
}) {
  return <form className="mb-4 flex flex-wrap items-end gap-3" onSubmit={event => {
    event.preventDefault();
    const name = String(new FormData(event.currentTarget).get("name") ?? "").trim();
    if (name) onSave(name);
  }}>
    <label className="block text-sm">{label}
      <input className={`${field} mt-1`} name="name" maxLength={120} required defaultValue={initial} autoFocus /></label>
    <button className={action} disabled={busy}>{submit}</button>
    <button type="button" className={secondary} onClick={onCancel}>Cancel</button>
  </form>;
}
