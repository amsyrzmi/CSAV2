"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { hrHref } from "@/lib/hr-paths";
import { peopleClient, type Person } from "@/lib/hr-people-api";
import { optionName, PEOPLE_PICKER_PAGES, personLabel, type Option } from "@/lib/hr-pickers";
import {
  processClient, type ProcessKind, type TemplateOut, type TemplatePreview, type TemplateReceipt, type TemplateSummary,
} from "@/lib/hr-process-api";
import { processLoadError, processSaveError } from "@/lib/hr-process-copy";
import { offsetSentence, processKindLabel } from "@/lib/hr-process-view";
import {
  APPROVAL_CHOICES, CHECK_CHOICES, EMPLOYMENT_TYPES, KIND_CHOICES, OWNER_CHOICES, REGION_LABELS,
  SYSTEM_CHECK_LABEL, copyDraft, createBody, draftFromTemplate, draftProblems, emptyDraft, hasProblems, legalEntities,
  previewText, rulesBody, rulesText, sameDraft, updateBody, warningText, withKind, type RuleDraft, type TemplateDraft,
} from "@/lib/hr-template-form";
import Dialog from "../fs/Dialog";
import StatusLabel from "../fs/StatusLabel";
import { action as primary, field, secondary, Notice, Panel } from "../ui";
import { StepsEditor, TextField } from "./StepsEditor";
import { isStale, NOTICE_FOCUS } from "./TaskActions";
import { useDepartments } from "./TemplateList";
import { useHrRunner } from "./useHrRunner";
import { useReturnFocus } from "./useReturnFocus";

/** Refusals that mean the template on screen is out of date: say so and read it again. */
const STALE: ReadonlySet<string> = new Set(["template_changed", "resource_unavailable"]);
const PREVIEW_DELAY = 400;
const card = "rounded-[var(--fs-radius-panel)] border border-[color:var(--fs-border)] bg-[var(--fs-surface)] p-3";
/** What a new template is compared with to tell whether it holds unsaved work. */
const BLANK = emptyDraft();

// "Copy as new" hands its draft to the new template's page in memory (no URL parameter): the page
// reads it when it opens, and the tab forgets it on any other page.
let copied: TemplateDraft | null = null;
/** Forget a copied draft (the tab calls this whenever the new template's page is not open). */
export function forgetCopiedDraft() { copied = null; }

// A new template opens on its own page after the save: that page shows the save's message and takes focus.
type Outcome = { message?: string; error?: string; warnings?: string[]; conflicts?: string[] };
let arrival: { templateId: string; outcome: Outcome } | null = null;
/** Forget the saved message of a new template unless `open` is that template's page. */
export function forgetTemplateArrival(open: string | null) {
  if (arrival && arrival.templateId !== open) arrival = null;
}

/** The people the editor names (the named-employee picker) and the legal entities on their records. */
function useTemplatePeople(tenant: string) {
  const [state, setState] = useState<{ options: Option[]; entities: string[]; loaded: boolean }>(
    { options: [], entities: [], loaded: false });
  useEffect(() => {
    let active = true;
    const people = peopleClient(tenant);
    (async () => {
      const items: Person[] = [];
      let cursor: string | undefined;
      for (let count = 0; count < PEOPLE_PICKER_PAGES; count += 1) {
        const page = await people.page({ cursor });
        items.push(...page.items);
        if (page.next_cursor === null) break;
        cursor = page.next_cursor;
      }
      return items;
    })()
      .then(items => {
        if (active) setState({
          options: items.map(person => [person.employee_id, personLabel(person)]), entities: legalEntities(items), loaded: true,
        });
      })
      .catch(() => { if (active) setState({ options: [], entities: [], loaded: true }); });
    return () => { active = false; };
  }, [tenant]);
  return state;
}

type Form = { draft: TemplateDraft; baseline: TemplateDraft; version: number; active: boolean };
type Load = { form: Form; template: TemplateOut | null } | { failed: string } | null;

/**
 * One template for the HR team (`templateId`, or null for a new one): name, kind (fixed after the
 * template is created), who it applies to with a live count of the people that matches now and the
 * templates it may tie with, and its steps. Save sends the whole template with the version it was
 * read at; a stale version says so and reads it again. Deactivate asks first; Copy as new opens a new
 * template with the same rules and steps. Leaving with unsaved changes asks first. A read-only member
 * sees the template without any control, and no count is asked for them.
 */
export function TemplateEditor({ tenant, templateId, readonly, platform }: {
  tenant: string; templateId: string | null; readonly: boolean; platform: boolean;
}) {
  const client = useMemo(() => processClient(tenant), [tenant]);
  const router = useRouter();
  const departments = useDepartments(tenant);
  const people = useTemplatePeople(tenant);
  const runner = useHrRunner();
  const isNew = templateId === null;
  const [load, setLoad] = useState<Load>(() => isNew
    ? { form: { draft: copied ?? emptyDraft(), baseline: BLANK, version: 0, active: true }, template: null } : null);
  const [reads, setReads] = useState(0);
  const [reloading, setReloading] = useState(false);
  const [summaries, setSummaries] = useState<TemplateSummary[]>([]);
  const [outcome, setOutcome] = useState<Outcome | null>(() =>
    templateId !== null && arrival?.templateId === templateId ? arrival.outcome : null);
  const arrived = useRef(outcome !== null);
  const [tried, setTried] = useState(false);
  const [ask, setAsk] = useState<{ opener: HTMLElement } | null>(null);
  const [dialogError, setDialogError] = useState("");
  const [leaving, setLeaving] = useState<string | null>(null);
  const [invalidFocus, setInvalidFocus] = useState(0);
  const panel = useRef<HTMLDivElement>(null);
  const notices = useRef<HTMLDivElement>(null);
  const formRef = useRef<HTMLFormElement>(null);
  const ids = useId();
  const returnFocus = useReturnFocus(!reloading && !runner.busy, notices, panel);

  useEffect(() => {
    if (templateId === null) return;
    let active = true;
    client.template(templateId)
      .then(template => {
        if (!active) return;
        const draft = draftFromTemplate(template);
        setLoad({ form: { draft, baseline: draft, version: template.version, active: template.is_active }, template });
      })
      .catch(cause => {
        if (!active) return;
        const text = processLoadError(cause);
        setLoad(old => old && "form" in old ? old : { failed: text });
        if (reads > 0) setOutcome(old => ({ ...old, error: [old?.error, text].filter(Boolean).join(" ") }));
      })
      .finally(() => { if (active) setReloading(false); });
    return () => { active = false; };
  }, [client, templateId, reads]);
  // The names of the templates a save says this one may tie with.
  useEffect(() => {
    let active = true;
    client.templates().then(list => { if (active) setSummaries(list.items); }).catch(() => undefined);
    return () => { active = false; };
  }, [client]);
  // A new template's page takes focus on its save message once it has loaded.
  const loaded = load !== null && "form" in load;
  useEffect(() => {
    if (!loaded || !arrived.current) return;
    arrived.current = false;
    arrival = null;
    returnFocus(null);
  }, [loaded, returnFocus]);
  // After a save the checks refused, the first field they marked takes focus (once the form is enabled again).
  const focusedInvalid = useRef(0);
  useEffect(() => {
    if (invalidFocus === focusedInvalid.current || runner.busy) return;
    focusedInvalid.current = invalidFocus;
    formRef.current?.querySelector<HTMLElement>('[aria-invalid="true"]')?.focus();
  }, [invalidFocus, runner.busy]);

  const form = load && "form" in load ? load.form : null;
  const dirty = Boolean(form && !readonly && !sameDraft(form.draft, form.baseline));
  // Leaving with unsaved changes: a link inside the console asks first, closing the page asks the browser's way.
  useEffect(() => {
    if (!dirty) return;
    const unload = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    const click = (event: MouseEvent) => {
      if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      const link = event.target instanceof Element ? event.target.closest("a[href]") : null;
      if (!(link instanceof HTMLAnchorElement) || (link.target && link.target !== "_self") || link.hasAttribute("download")) return;
      const url = new URL(link.href, window.location.href);
      if (url.origin !== window.location.origin) return;
      if (url.pathname === window.location.pathname && url.search === window.location.search) return;
      event.preventDefault();
      event.stopPropagation();
      setLeaving(`${url.pathname}${url.search}${url.hash}`);
    };
    window.addEventListener("beforeunload", unload);
    window.addEventListener("click", click, true);
    return () => { window.removeEventListener("beforeunload", unload); window.removeEventListener("click", click, true); };
  }, [dirty]);

  // The live count: debounced, and only for those who may edit (the read is refused to read-only members).
  const kind = form?.draft.kind ?? "onboarding";
  const problems = form ? draftProblems(form.draft) : null;
  const rules = form ? rulesBody(form.draft.rules) : null;
  const previewKey = JSON.stringify([kind, rules]);
  const previewable = Boolean(form) && !readonly && !problems?.legal_entity;
  const [preview, setPreview] = useState<{ key: string; result: TemplatePreview } | { key: string; failed: string } | null>(null);
  useEffect(() => {
    if (!previewable) return;
    let active = true;
    const [askKind, askRules] = JSON.parse(previewKey) as [ProcessKind, ReturnType<typeof rulesBody>];
    const timer = window.setTimeout(() => {
      client.previewTemplate(askKind, askRules)
        .then(result => { if (active) setPreview({ key: previewKey, result }); })
        .catch(cause => { if (active) setPreview({ key: previewKey, failed: processLoadError(cause) }); });
    }, PREVIEW_DELAY);
    return () => { active = false; window.clearTimeout(timer); };
  }, [client, previewKey, previewable]);

  const reload = useCallback(() => { setReloading(true); setReads(count => count + 1); }, []);
  const listHref = hrHref({ tab: "processes", view: "templates" }, platform, tenant);
  const back = <Link className="inline-block text-sm underline" href={listHref}>All templates</Link>;

  if (readonly && isNew) return <div data-testid="hr-template-editor" className="space-y-3">
    {back}
    <Notice>Your account can only view templates.</Notice>
  </div>;
  if (!load) return <div data-testid="hr-template-editor"><Notice>Loading the template…</Notice></div>;
  if ("failed" in load) return <div ref={panel} data-testid="hr-template-editor" className="space-y-3">
    {back}
    <Panel title="Template">
      <div className="space-y-3">
        <Notice error>{load.failed}</Notice>
        <button type="button" className={secondary} onClick={reload}>Try again</button>
      </div>
    </Panel>
  </div>;

  const { draft } = load.form;
  const current = load.form;
  const shown = tried && problems ? problems : null;
  const nameOf = (id: string) => summaries.find(item => item.template_id === id)?.name ?? "another template";
  const set = (next: TemplateDraft) => setLoad({ ...load, form: { ...current, draft: next } });
  const setRules = (patch: Partial<RuleDraft>) => set({ ...draft, rules: { ...draft.rules, ...patch } });
  const busy = runner.busy || reloading;

  if (readonly) return <div ref={panel} data-testid="hr-template-editor" className="space-y-3">
    {back}
    <TemplateView template={load.template} draft={draft} active={current.active} people={people.options}
      departmentName={departments.name} />
  </div>;

  const saved = (receipt: TemplateReceipt, saving: TemplateDraft): Outcome => ({
    message: "Template saved.",
    warnings: receipt.warnings.map(warning => warningText(warning,
      ordinal => saving.steps[ordinal - 1]?.title_en.trim() || null)),
    conflicts: receipt.conflicts,
  });
  const save = (activate: boolean) => runner.run(async () => {
    // Cleared first: the same refusal twice is a new message, and takes focus again.
    setOutcome(null);
    const saving = draft;
    if (hasProblems(draftProblems(saving))) {
      setTried(true);
      setOutcome({ error: "Check the fields marked below." });
      setInvalidFocus(count => count + 1);
      return;
    }
    try {
      if (templateId === null) {
        const receipt = await client.createTemplate(createBody(saving));
        arrival = { templateId: receipt.template_id, outcome: saved(receipt, saving) };
        setLoad({ ...load, form: { ...current, baseline: saving } });
        router.replace(hrHref({ tab: "processes", view: "template", templateId: receipt.template_id }, platform, tenant));
        return;
      }
      const receipt = await client.updateTemplate(templateId, updateBody(saving, current.version, activate));
      setLoad({ ...load, form: { draft: saving, baseline: saving, version: receipt.version, active: activate || current.active } });
      setOutcome(saved(receipt, saving));
      setTried(false);
    } catch (cause) {
      setOutcome({ error: processSaveError(cause, "save") });
      if (isStale(cause, STALE)) reload();
    }
    returnFocus(null);
  });
  const deactivate = (opener: HTMLElement) => runner.run(async () => {
    if (templateId === null) return;
    setOutcome(null);
    setDialogError("");
    try {
      const receipt = await client.deactivateTemplate(templateId, current.version);
      setAsk(null);
      setLoad({ ...load, form: { ...current, version: receipt.version, active: false } });
      setOutcome({ message: "Template deactivated. New processes no longer use it." });
      returnFocus(opener);
    } catch (cause) {
      const text = processSaveError(cause, "deactivate");
      if (isStale(cause, STALE)) {
        setAsk(null);
        setOutcome({ error: text });
        returnFocus(opener);
        reload();
      } else setDialogError(text);
    }
  });
  const copyAsNew = () => {
    copied = copyDraft(draft);
    router.push(hrHref({ tab: "processes", view: "new-template" }, platform, tenant));
  };

  const ties = preview && preview.key === previewKey && "result" in preview
    ? preview.result.ties.filter(tie => tie.template_id !== templateId) : [];
  const id = (name: string) => `${ids}-${name}`;
  const select = "block text-sm font-medium";
  const departmentChoices = departments.items.filter(item => !item.archived || item.department_id === draft.rules.department_id);

  return <div ref={panel} data-testid="hr-template-editor" className="space-y-3" aria-busy={busy}>
    {back}
    <Panel title={isNew ? "New template" : current.baseline.name || "Template"}>
      <div className="space-y-5">
        {outcome && <div ref={notices} tabIndex={-1} data-testid="hr-template-outcome" className={`space-y-3 ${NOTICE_FOCUS}`}>
          {outcome.message && <Notice testId="hr-template-notice">{outcome.message}</Notice>}
          {(outcome.warnings?.length || outcome.conflicts?.length) ? <div data-testid="hr-template-warnings" className="space-y-2">
            {outcome.warnings?.map(text => <Notice key={text} warning>{text}</Notice>)}
            {outcome.conflicts?.length ? <Notice warning>May tie with {outcome.conflicts.map(nameOf).join(", ")}. When both match
              a person, the older template is used.</Notice> : null}
          </div> : null}
          {outcome.error && <Notice error testId="hr-template-error">{outcome.error}</Notice>}
        </div>}
        <form ref={formRef} noValidate className="space-y-5" onSubmit={event => { event.preventDefault(); void save(false); }}>
          <fieldset disabled={busy} className="min-w-0 space-y-5">
            <div className="grid gap-3 sm:grid-cols-2">
              <TextField id={id("name")} name="name" label="Name" value={draft.name} problem={shown?.name}
                onChange={name => set({ ...draft, name })} />
              {isNew ? <div>
                <label className={select} htmlFor={id("kind")}>Kind</label>
                <select id={id("kind")} className={`${field} mt-1`} value={draft.kind}
                  onChange={event => set(withKind(draft, event.target.value as ProcessKind))}>
                  {KIND_CHOICES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                </select>
              </div> : <dl className="text-sm">
                <dt className="font-medium">Kind</dt>
                <dd className="mt-1 flex flex-wrap items-center gap-2"><span>{processKindLabel(draft.kind)}</span>
                  <StatusLabel tone={current.active ? "success" : "neutral"}>{current.active ? "Active" : "Inactive"}</StatusLabel></dd>
              </dl>}
            </div>
            <fieldset className="min-w-0 space-y-3">
              <legend className="mb-1 font-semibold">Applies to</legend>
              <p className="text-sm text-[color:var(--fs-text-secondary)]">Leave every rule on Any to make this the company&apos;s
                default. When several templates match a person, the one with the most rules is used.</p>
              <div className="grid gap-3 sm:grid-cols-2">
                <div>
                  <label className={select} htmlFor={id("department")}>Department</label>
                  <select id={id("department")} className={`${field} mt-1`} value={draft.rules.department_id}
                    onChange={event => setRules({ department_id: event.target.value })}>
                    <option value="">Any department</option>
                    {departmentChoices.map(item => <option key={item.department_id} value={item.department_id}>
                      {item.name}{item.archived ? " (archived)" : ""}</option>)}
                  </select>
                </div>
                <div>
                  <label className={select} htmlFor={id("type")}>Employment type</label>
                  <select id={id("type")} className={`${field} mt-1`} value={draft.rules.employment_type}
                    onChange={event => setRules({ employment_type: event.target.value as RuleDraft["employment_type"] })}>
                    <option value="">Any type</option>
                    {EMPLOYMENT_TYPES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                  </select>
                </div>
                <TextField id={id("entity")} name="legal_entity_reference" label="Legal entity" list={id("entities")}
                  value={draft.rules.legal_entity_reference} problem={problems?.legal_entity}
                  onChange={legal_entity_reference => setRules({ legal_entity_reference })} />
                <datalist id={id("entities")}>{people.entities.map(entity => <option key={entity} value={entity} />)}</datalist>
                <div>
                  <label className={select} htmlFor={id("region")}>Region</label>
                  <select id={id("region")} className={`${field} mt-1`} value={draft.rules.region}
                    onChange={event => setRules({ region: event.target.value as RuleDraft["region"] })}>
                    <option value="">Any region</option>
                    {Object.entries(REGION_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                  </select>
                </div>
              </div>
              <div data-testid="hr-template-preview" aria-live="polite" className="space-y-1 text-sm">
                {problems?.legal_entity ? <p>Fix the legal entity to see who this applies to.</p>
                  : !preview || preview.key !== previewKey ? <p className="text-[color:var(--fs-text-secondary)]">Counting the people…</p>
                    : "failed" in preview ? <p>{preview.failed}</p>
                      : <>
                        <p className="font-medium">{previewText(preview.result)}</p>
                        {ties.length > 0 && <>
                          <ul className="space-y-1">{ties.map(tie => <li key={tie.template_id}
                            className="text-[color:var(--fs-warning)] [overflow-wrap:anywhere]">May tie with {tie.name}</li>)}</ul>
                          <p className="text-[color:var(--fs-text-secondary)]">When both match a person, the older template is used.</p>
                        </>}
                      </>}
              </div>
            </fieldset>
            <StepsEditor kind={draft.kind} steps={draft.steps} problems={shown?.steps ?? []} people={people.options}
              peopleLoaded={people.loaded} onChange={steps => set({ ...draft, steps })} />
          </fieldset>
          <div className="flex flex-wrap gap-2">
            <button type="submit" className={primary} disabled={busy}>Save</button>
            {!isNew && !current.active && <button type="button" className={secondary} disabled={busy}
              onClick={() => void save(true)}>Save and activate</button>}
            {!isNew && <button type="button" className={secondary} disabled={busy} onClick={copyAsNew}>Copy as new</button>}
            {!isNew && current.active && <button type="button" className={secondary} disabled={busy}
              onClick={event => { setDialogError(""); setAsk({ opener: event.currentTarget }); }}>Deactivate</button>}
          </div>
        </form>
      </div>
    </Panel>
    {ask && <Dialog open onClose={() => { if (!runner.busy) setAsk(null); }} testId="hr-template-dialog" title="Deactivate template">
      <div className="space-y-3 text-sm">
        <p className="font-medium [overflow-wrap:anywhere]">Deactivate {current.baseline.name}?</p>
        <p>Processes already running keep their steps. New processes will not use this template.</p>
        {dialogError && <Refusal text={dialogError} />}
        <div className="flex flex-wrap gap-2">
          <button type="button" className={primary} disabled={runner.busy} onClick={() => void deactivate(ask.opener)}>
            Deactivate</button>
          <button type="button" className={secondary} disabled={runner.busy} onClick={() => setAsk(null)}>Keep it</button>
        </div>
      </div>
    </Dialog>}
    {leaving && <Dialog open onClose={() => setLeaving(null)} testId="hr-template-discard" title="Discard your changes?">
      <div className="space-y-3 text-sm">
        <p>Your changes to this template are not saved.</p>
        <div className="flex flex-wrap gap-2">
          <button type="button" className={primary} onClick={() => { const to = leaving; setLeaving(null); router.push(to); }}>
            Discard changes</button>
          <button type="button" className={secondary} onClick={() => setLeaving(null)}>Keep editing</button>
        </div>
      </div>
    </Dialog>}
  </div>;
}

/** A dialog's refusal, which takes focus (the button it came from was off while the write ran). */
function Refusal({ text }: { text: string }) {
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => { box.current?.focus(); }, [text]);
  return <div ref={box} tabIndex={-1} className={NOTICE_FOCUS}><Notice error>{text}</Notice></div>;
}

const label = <T extends string>(choices: [T, string][], value: T) => choices.find(([key]) => key === value)?.[1] ?? "";

/** The template as a read-only member sees it: every field in words, no control. */
function TemplateView({ template, draft, active, people, departmentName }: {
  template: TemplateOut | null;
  draft: TemplateDraft;
  active: boolean;
  people: Option[];
  departmentName: (departmentId: string) => string | null;
}) {
  const line = "whitespace-pre-line text-sm [overflow-wrap:anywhere]";
  return <Panel title={draft.name || "Template"}>
    <div className="space-y-5">
      <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
        <div><dt className="text-[color:var(--fs-text-secondary)]">Kind</dt><dd>{processKindLabel(draft.kind)}</dd></div>
        <div><dt className="text-[color:var(--fs-text-secondary)]">Status</dt>
          <dd><StatusLabel tone={active ? "success" : "neutral"}>{active ? "Active" : "Inactive"}</StatusLabel></dd></div>
        <div className="sm:col-span-2"><dt className="text-[color:var(--fs-text-secondary)]">Applies to</dt>
          <dd className="[overflow-wrap:anywhere]">{rulesText(template?.rules ?? {}, departmentName)}</dd></div>
      </dl>
      <section aria-labelledby="hr-template-view-steps">
        <h3 id="hr-template-view-steps" className="mb-3 font-semibold">Steps</h3>
        <ol className="space-y-3">{draft.steps.map((step, index) => {
          const owner = step.owner_role === "person"
            ? optionName(people, step.owner_employee_id) ?? "A named employee" : label(OWNER_CHOICES, step.owner_role);
          const check = step.auto_check === "access_removed" ? SYSTEM_CHECK_LABEL : label(CHECK_CHOICES, step.auto_check);
          return <li key={step.key} data-testid="hr-template-step" className={card}>
            <p className="text-xs text-[color:var(--fs-text-secondary)]">Step {index + 1}{step.required ? "" : " · optional"}</p>
            <p className="font-medium [overflow-wrap:anywhere]">{step.title_en}</p>
            {step.title_ms && <p lang="ms" className={`${line} text-[color:var(--fs-text-secondary)]`}>{step.title_ms}</p>}
            {step.description_en && <p className={`${line} mt-1`}>{step.description_en}</p>}
            {step.description_ms && <p lang="ms" className={`${line} mt-1 text-[color:var(--fs-text-secondary)]`}>{step.description_ms}</p>}
            <p className={`${line} mt-1`}>Owner: {owner}</p>
            <p className={line}>Due: {offsetSentence(step.anchor, Number(step.due_offset_days))}</p>
            {step.approval_by && <p className={line}>Approval by: {label(APPROVAL_CHOICES, step.approval_by)}</p>}
            {step.auto_check && <p className={line}>Automatic check: {check}</p>}
            {step.reference_note && <p className={line}>Reference: {step.reference_note}</p>}
          </li>;
        })}</ol>
      </section>
    </div>
  </Panel>;
}
