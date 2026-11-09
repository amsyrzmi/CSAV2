"use client";

import { useEffect, useState } from "react";
import {
  changedPersona,
  MODE_CHOICES,
  panelWarnings,
  readReplies,
  saveBlockedReason,
  saveErrorText,
  saveReplies,
  type CustomerReplies,
  type PersonaFields,
  type ReplyMode,
} from "@/lib/customer-replies";
import { action, field, Notice, Panel } from "./ui";

/** Persona inputs: the label, a hint about the default, and a test id suffix. */
const PERSONA_INPUTS: {
  key: keyof PersonaFields; label: string; hint: string; testId: string; max: number;
}[] = [
  { key: "persona_name", label: "Persona name", testId: "persona-name", max: 60,
    hint: "The name your bot signs with. Leave empty to use your brand name." },
  { key: "brand_tone", label: "Tone", testId: "tone", max: 200,
    hint: "Default: brief, warm and plain. For example: friendly and calm." },
  { key: "intro_line", label: "Greeting", testId: "greeting", max: 300,
    hint: "Said when a customer greets the bot or asks who they are talking to." },
];

/** The brand name, the bot's voice and whether it answers customers. Only administrators can change them. */
export function CustomerRepliesPanel({ tenantId }: { tenantId: string | null }) {
  const [replies, setReplies] = useState<CustomerReplies | null>(null);
  const [brandName, setBrandName] = useState("");
  const [persona, setPersona] = useState<PersonaFields>({ persona_name: "", brand_tone: "", intro_line: "" });
  const [mode, setMode] = useState<ReplyMode>("off");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);

  function show(value: CustomerReplies) {
    setReplies(value);
    setBrandName(value.brand_name ?? "");
    // A read from an older server has no persona fields: they read as empty.
    setPersona({
      persona_name: value.persona_name ?? "",
      brand_tone: value.brand_tone ?? "",
      intro_line: value.intro_line ?? "",
    });
    setMode(value.mode);
  }
  useEffect(() => {
    const controller = new AbortController();
    readReplies(tenantId, controller.signal).then(show).catch(() => {
      if (!controller.signal.aborted) setError("We couldn't load customer replies. Reload to try again.");
    });
    return () => controller.abort();
  }, [tenantId]);

  const blocked = saveBlockedReason(mode, brandName);
  async function save(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    setSaved(false);
    try {
      show(await saveReplies(tenantId, {
        mode,
        brand_name: brandName.trim(),
        ...(replies ? changedPersona(replies, persona) : {}),
      }));
      setSaved(true);
    } catch (cause) {
      setError(saveErrorText(cause));
    } finally {
      setBusy(false);
    }
  }

  const current = replies && MODE_CHOICES.find((choice) => choice.mode === replies.mode);
  return (
    <Panel title="Customer replies">
      <div data-testid="customer-replies-panel" className="space-y-3">
        {error && <Notice error>{error}</Notice>}
        {!replies ? (error ? null : <Notice>Loading customer replies...</Notice>) : <>
          {panelWarnings(replies.warnings).map((line) => <Notice key={line}>{line}</Notice>)}
          {replies.can_manage ? (
            <form className="space-y-3" onSubmit={(event) => void save(event)}>
              <label className="block text-sm">The name your bot uses with customers
                <input className={`mt-1 ${field}`} value={brandName} maxLength={120}
                  data-testid="customer-replies-brand"
                  onChange={(event) => setBrandName(event.target.value)} />
              </label>
              {PERSONA_INPUTS.map((input) => (
                <label key={input.key} className="block text-sm">{input.label}
                  <input className={`mt-1 ${field}`} value={persona[input.key]} maxLength={input.max}
                    data-testid={`customer-replies-${input.testId}`}
                    onChange={(event) => setPersona({ ...persona, [input.key]: event.target.value })} />
                  <span className="mt-1 block text-ink-500">{input.hint}</span>
                </label>
              ))}
              <fieldset className="space-y-2 text-sm">
                <legend className="mb-1">Replies</legend>
                {MODE_CHOICES.map((choice) => (
                  <label key={choice.mode} className="flex items-start gap-2">
                    <input type="radio" name="customer-replies-mode" className="mt-1"
                      data-testid={`customer-replies-${choice.mode}`}
                      checked={mode === choice.mode} onChange={() => setMode(choice.mode)} />
                    <span>{choice.label}</span>
                  </label>
                ))}
              </fieldset>
              {blocked && <p className="text-sm text-ink-500">{blocked}</p>}
              {saved && <p className="text-sm" role="status">Saved.</p>}
              <button className={action} disabled={busy || blocked !== null}
                data-testid="customer-replies-save">Save</button>
            </form>
          ) : (
            <div className="space-y-1 text-sm" data-testid="customer-replies-state">
              <p>Brand name: {replies.brand_name ?? "Not set"}</p>
              {PERSONA_INPUTS.map((input) => (
                <p key={input.key} data-testid={`customer-replies-state-${input.testId}`}>
                  {input.label}: {replies[input.key] || "Not set"}
                </p>
              ))}
              <p>Replies: {current?.label}</p>
              <p>Ask your admin to change this.</p>
            </div>
          )}
        </>}
      </div>
    </Panel>
  );
}
