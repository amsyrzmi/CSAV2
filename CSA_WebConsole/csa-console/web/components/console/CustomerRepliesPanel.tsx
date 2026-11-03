"use client";

import { useEffect, useState } from "react";
import {
  MODE_CHOICES,
  panelWarnings,
  readReplies,
  saveBlockedReason,
  saveErrorText,
  saveReplies,
  type CustomerReplies,
  type ReplyMode,
} from "@/lib/customer-replies";
import { action, field, Notice, Panel } from "./ui";

/** The brand name and whether the bot answers customers. Only administrators can change them. */
export function CustomerRepliesPanel({ tenantId }: { tenantId: string | null }) {
  const [replies, setReplies] = useState<CustomerReplies | null>(null);
  const [brandName, setBrandName] = useState("");
  const [mode, setMode] = useState<ReplyMode>("off");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);

  function show(value: CustomerReplies) {
    setReplies(value);
    setBrandName(value.brand_name ?? "");
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
      show(await saveReplies(tenantId, { mode, brand_name: brandName.trim() }));
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
              <p>Replies: {current?.label}</p>
              <p>Ask your tenant admin to change this.</p>
            </div>
          )}
        </>}
      </div>
    </Panel>
  );
}
