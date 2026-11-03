"use client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError } from "@/lib/api";
import { hrClient } from "@/lib/hr-api";
import { dateText } from "@/lib/hr-people";
import {
  PERSONAL_FIELDS, personalBody, personalFormValues, personalSavedText, recordsErrorText, type PersonalFieldSpec,
  type PersonalFormValues,
} from "@/lib/hr-records";
import { recordsClient, type EncryptedField, type Personal } from "@/lib/hr-records-api";
import { action, field as fieldClass, secondary, Notice, Panel } from "../ui";

type Loaded = { personal: Personal } | { failed: true } | null;
type Revealed = Partial<Record<EncryptedField, string | null>>;

const NOT_SET = "Not set";
const MASK_PLACEHOLDER = "Leave blank to keep";
/** The longest text each plain box takes (the server's limits), so a long entry stops at the box. */
const MAX_LENGTH: Partial<Record<string, number>> = {
  home_address: 500, emergency_name: 120, emergency_relationship: 60, bank_name: 100, personal_phone: 40, emergency_phone: 40,
};

/** The date of birth reads like the other dates on the profile; every other value is shown as stored. */
function plainText(spec: PersonalFieldSpec, value: string | null): string {
  if (!value) return NOT_SET;
  return spec.id === "date_of_birth" ? dateText(value) : value;
}

/**
 * A person's personal details: the plain fields, and the five numbers masked with an audited Show.
 * A shown number stays on the page only until Hide, Edit, or the section reloads. The server decides
 * what the viewer may edit (`can_edit`): the person themself gets their contact fields, HR gets all.
 * The form's rules live in `lib/hr-records.ts`.
 */
export function PersonalSection({ tenant, employeeId, readonly, title = "Personal details" }: {
  tenant: string; employeeId: string; readonly: boolean; title?: string;
}) {
  const records = useMemo(() => recordsClient(tenant), [tenant]);
  const [loaded, setLoaded] = useState<Loaded>(null);
  const [revealed, setRevealed] = useState<Revealed>({});
  const [revealing, setRevealing] = useState<EncryptedField | null>(null);
  const [revealError, setRevealError] = useState("");
  const [editing, setEditing] = useState(false);
  const [message, setMessage] = useState("");
  const [reloadFailed, setReloadFailed] = useState(false);
  useEffect(() => {
    let active = true;
    records.personal(employeeId)
      .then(personal => { if (active) setLoaded({ personal }); })
      .catch(() => { if (active) setLoaded({ failed: true }); });
    return () => { active = false; };
  }, [records, employeeId]);
  const reload = useCallback(async () => {
    try {
      const personal = await records.personal(employeeId);
      setLoaded({ personal }); setRevealed({}); setReloadFailed(false);
    } catch { setReloadFailed(true); }
  }, [records, employeeId]);

  async function show(id: EncryptedField) {
    if (revealing) return;
    setRevealing(id); setRevealError("");
    try {
      const { value } = await records.reveal(employeeId, id);
      setRevealed(current => ({ ...current, [id]: value }));
    } catch { setRevealError("We could not show that number. Try again."); }
    finally { setRevealing(null); }
  }
  function hide(id: EncryptedField) {
    setRevealed(current => Object.fromEntries(Object.entries(current).filter(([key]) => key !== id)));
  }

  if (!loaded) return <Notice>Loading personal details…</Notice>;
  if ("failed" in loaded) return <Notice error>We could not load personal details. Reload the page and try again.</Notice>;
  const { personal } = loaded;
  const editable = !readonly;
  if (editing) return <PersonalForm key={personal.version ?? 0} tenant={tenant} employeeId={employeeId} personal={personal}
    title={title} onCancel={() => { setEditing(false); setRevealed({}); }} onReload={() => { void reload(); }}
    onSaved={saved => { setEditing(false); setMessage(saved); void reload(); }} />;

  const empty = personal.version === null;
  return <Panel title={title}>
    {message && <div className="mb-4"><Notice>{message}</Notice></div>}
    {reloadFailed && <div className="mb-4"><Notice error>We could not reload the details. Reload the page and try again.</Notice></div>}
    {revealError && <div className="mb-4"><Notice error>{revealError}</Notice></div>}
    {empty ? <p className="text-sm text-ink-500">No personal details yet.</p>
      : <dl className="grid gap-x-6 gap-y-3 text-sm sm:grid-cols-[max-content_1fr]">
        {PERSONAL_FIELDS.map(spec => {
          const value = personal[spec.id];
          const id = spec.id as EncryptedField;
          const shown = spec.encrypted && Object.hasOwn(revealed, id);
          return <div key={spec.id} className="contents">
            <dt className="text-ink-500">{spec.label}</dt>
            <dd className="flex flex-wrap items-center gap-3 font-medium">
              <span className={spec.id === "home_address" ? "whitespace-pre-line" : undefined}>
                {spec.encrypted ? (shown ? revealed[id] ?? NOT_SET : value ?? NOT_SET) : plainText(spec, value)}</span>
              {spec.encrypted && value && (shown
                ? <button type="button" className={secondary} aria-label={`Hide ${spec.label}`} onClick={() => hide(id)}>Hide</button>
                : <button type="button" className={secondary} aria-label={`Show ${spec.label}`} disabled={revealing !== null}
                  onClick={() => void show(id)}>Show</button>)}
            </dd>
          </div>;
        })}
      </dl>}
    {editable && <button type="button" className={`${action} mt-5`}
      onClick={() => { setEditing(true); setRevealed({}); setRevealError(""); setMessage(""); }}>
      {empty ? "Add details" : "Edit"}</button>}
  </Panel>;
}

type Problem = { text: string; code: string };

/**
 * The edit form. Plain fields start from the stored values; the five number boxes start empty (the page
 * never holds a stored number), and a stored number gets a Clear checkbox. A stale save keeps what was
 * typed and offers Reload; the client keeps the Idempotency-Key for the same body, so a retry is safe.
 */
function PersonalForm({ tenant, employeeId, personal, title, onSaved, onReload, onCancel }: {
  tenant: string; employeeId: string; personal: Personal; title: string;
  onSaved: (message: string) => void; onReload: () => void; onCancel: () => void;
}) {
  const client = useMemo(() => hrClient(tenant), [tenant]);
  const initial = useMemo(() => personalFormValues(personal), [personal]);
  const mode = personal.can_edit;
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<Problem | null>(null);
  const submitting = useRef(false);
  const specs = PERSONAL_FIELDS.filter(spec => mode === "all" || spec.self);

  async function submit(form: HTMLFormElement) {
    if (submitting.current) return;
    const data = new FormData(form);
    const values = { ...initial };
    const cleared = new Set<EncryptedField>();
    for (const spec of specs) {
      values[spec.id] = String(data.get(spec.id) ?? "");
      if (spec.encrypted && data.has(`clear_${spec.id}`)) cleared.add(spec.id as EncryptedField);
    }
    submitting.current = true; setBusy(true); setProblem(null);
    let receiptVersion: number;
    try {
      receiptVersion = (await client.operation(personalBody(values as PersonalFormValues, personal, mode, employeeId, cleared))).version;
    } catch (cause) {
      const failure = cause instanceof ApiError ? { status: cause.status, code: cause.code } : null;
      setProblem({ text: recordsErrorText(failure, "save"), code: failure?.code ?? "" });
      return;
    } finally { submitting.current = false; setBusy(false); }
    onSaved(personalSavedText(receiptVersion));
  }

  return <Panel title={title}>
    <form onSubmit={event => { event.preventDefault(); void submit(event.currentTarget); }}>
      <fieldset disabled={busy} className="grid gap-4 sm:grid-cols-2">
        {mode === "contact" && <p className="text-sm text-ink-500 sm:col-span-2">
          You can change your address, personal phone and emergency contact. HR keeps the rest.</p>}
        {specs.map(spec => <div key={spec.id} className={spec.id === "home_address" ? "sm:col-span-2" : undefined}>
          <label className="block text-sm">{spec.label}
            {spec.id === "home_address"
              ? <textarea className={`${fieldClass} mt-1 h-auto py-2`} name={spec.id} rows={3} maxLength={MAX_LENGTH[spec.id]} defaultValue={initial[spec.id]} />
              : <input className={`${fieldClass} mt-1`} name={spec.id} autoComplete="off"
                type={spec.id === "date_of_birth" ? "date" : spec.id.endsWith("phone") ? "tel" : "text"}
                maxLength={MAX_LENGTH[spec.id]} defaultValue={initial[spec.id]}
                placeholder={spec.encrypted ? MASK_PLACEHOLDER : undefined} />}
          </label>
          {spec.encrypted && personal[spec.id] && <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
            <span className="text-ink-500">Stored: {personal[spec.id]}</span>
            <label className="flex items-center gap-2">
              <input type="checkbox" name={`clear_${spec.id}`} className="h-4 w-4" aria-label={`Clear ${spec.label}`} />
              Clear</label>
          </div>}
        </div>)}
        {problem && <div className="space-y-2 sm:col-span-2">
          <Notice error>{problem.text}</Notice>
          {problem.code === "person_changed" && <button type="button" className={secondary} onClick={onReload}>Reload</button>}
        </div>}
        <div className="flex flex-wrap gap-2 sm:col-span-2">
          <button className={action}>Save changes</button>
          <button type="button" className={secondary} onClick={onCancel}>Cancel</button>
        </div>
      </fieldset>
    </form>
  </Panel>;
}
