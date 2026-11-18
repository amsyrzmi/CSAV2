"use client";
import Link from "next/link";
import { Fragment, useEffect, useId, useRef, useState, type RefObject } from "react";
import { hrHref } from "@/lib/hr-paths";
import type { DefaultTemplatesReceipt } from "@/lib/hr-process-api";
import { processSaveError } from "@/lib/hr-process-copy";
import { defaultsSkippedText } from "@/lib/hr-process-view";
import Dialog from "../fs/Dialog";
import { action as primary, secondary, Notice } from "../ui";
import { isStale, NOTICE_FOCUS, type ProcessClient } from "./TaskActions";
import { useHrRunner } from "./useHrRunner";

/** The route answers 404 `resource_unavailable` while the deploy switch is off. */
const SWITCHED_OFF: ReadonlySet<string> = new Set(["resource_unavailable"]);

/**
 * The names of the two templates the server creates (csa/hr/default_templates.py). The dialog is a
 * static summary: the steps and their sources live in the templates themselves, which the list
 * shows once they exist, so there is no copy of the step data here to drift. The browser test
 * checks these names against the Python content.
 */
const NAMES = { onboarding: "Onboarding (Malaysia default)", offboarding: "Offboarding (Malaysia default)" };

/** A dialog's refusal, which takes focus (the confirm button was off while the write ran). */
function Refusal({ text }: { text: string }) {
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => { box.current?.focus(); }, [text]);
  return <div ref={box} tabIndex={-1} className={NOTICE_FOCUS}><Notice error>{text}</Notice></div>;
}

/**
 * "Create the default templates" for the HR team's template list: the button (only while `show`), the
 * dialog that says what it creates, and the notice that says what it did. Renders into the list's
 * toolbar row (a fragment), so nothing is left behind when there is nothing to show. One request per
 * open dialog: the client keeps the Idempotency-Key until a request succeeds, and a click while one
 * runs is ignored. `onChanged` asks the list to read again; `fallback` takes focus when the button
 * is gone by the time the dialog closes.
 */
export function DefaultTemplates({ client, tenant, platform, show, fallback, onChanged }: {
  client: ProcessClient;
  tenant: string;
  platform: boolean;
  show: boolean;
  fallback: RefObject<HTMLElement | null>;
  onChanged: () => void;
}) {
  const runner = useHrRunner();
  const { busy, run } = runner;
  const id = useId();
  const [asked, setAsked] = useState(false);
  const [error, setError] = useState<{ text: string; attempt: number } | null>(null);
  const [result, setResult] = useState<DefaultTemplatesReceipt | null>(null);
  const [arrived, setArrived] = useState(0);
  const notice = useRef<HTMLDivElement>(null);
  // Where the dialog gives focus back: the button that opened it (null), or `fallback` once it is gone.
  const back = useRef<HTMLElement | null>(null);
  useEffect(() => { if (arrived) notice.current?.focus(); }, [arrived]);

  const href = (templateId: string) => hrHref({ tab: "processes", view: "template", templateId }, platform, tenant);
  const close = () => { if (!busy) setAsked(false); };
  const create = () => run(async () => {
    setError(null);
    try {
      const receipt = await client.createDefaultTemplates();
      setResult(receipt);
      setAsked(false);
      setArrived(count => count + 1);
      onChanged();
    } catch (cause) {
      // A new attempt each time: the same refusal twice still takes focus (the button was off while it ran).
      setError(previous => ({ text: processSaveError(cause, "create-defaults"), attempt: (previous?.attempt ?? 0) + 1 }));
      if (isStale(cause, SWITCHED_OFF)) {
        // The switch is off now: the list reads again and drops the button.
        back.current = fallback.current;
        onChanged();
      }
    }
  });

  const skipped = result ? defaultsSkippedText(result) : "";
  return <>
    {show && <button type="button" className={secondary}
      onClick={() => { back.current = null; setError(null); setResult(null); setAsked(true); }}>Create the default templates</button>}
    {result && <div ref={notice} tabIndex={-1} data-testid="hr-defaults-notice" className={`basis-full ${NOTICE_FOCUS}`}>
      <Notice>
        {result.created.length > 0 && <>
          Created {result.created.map((item, index) => <Fragment key={item.template_id}>
            {index > 0 && " and "}<Link className="font-medium underline" href={href(item.template_id)}>{item.name}</Link>
          </Fragment>)}.{" "}
        </>}
        {skipped}{skipped && result.created.length > 0 && " "}
        {result.created.length > 0 && "Open a template to see every step, who does it, when it is due and its reference note, and to edit it."}
      </Notice>
    </div>}
    {asked && <Dialog open onClose={close} testId="hr-defaults-dialog" title="Create the default templates"
      describedBy={`${id}-what`} returnFocusTo={back}>
      <div className="space-y-3 text-sm">
        <p>This adds two templates for a company in Malaysia:</p>
        <ul className="list-disc space-y-1 pl-5">
          <li className="[overflow-wrap:anywhere]"><span className="font-medium">{NAMES.onboarding}</span>, for a person who is starting.</li>
          <li className="[overflow-wrap:anywhere]"><span className="font-medium">{NAMES.offboarding}</span>, for a person who is leaving.</li>
        </ul>
        <p id={`${id}-what`}>Each is a checklist of reminders. A step says who does it, when it is due and, for a legal step,
          the official source and the date it was checked. They are reminders with references: they do not file anything with an
          authority, and they are not legal advice.</p>
        <p>Neither has an &quot;Applies to&quot; rule, so each becomes a company default: the next person you add starts
          onboarding with real tasks and reminders, and marking someone as leaving starts the offboarding. If the company already
          has an active template of one kind, that kind is left as it is.</p>
        <p>You can edit both afterwards. The template list then shows each one with all its steps and reference notes.</p>
        {error && <Refusal key={error.attempt} text={error.text} />}
        <div className="flex flex-wrap gap-2">
          <button type="button" className={primary} disabled={busy} onClick={() => void create()}>Create the default templates</button>
          <button type="button" className={secondary} disabled={busy} onClick={close}>Cancel</button>
        </div>
      </div>
    </Dialog>}
  </>;
}
