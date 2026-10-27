"use client";

import { useEffect, useState } from "react";
import {
  connectWhatsAppNumber,
  disconnectWhatsAppNumber,
  getWhatsAppNumber,
  type WhatsAppNumber,
} from "@/lib/channels-api";
import { action, field, Notice, Panel, secondary } from "./ui";

const EMPTY = { phone_number_id: "", waba_id: "", access_token: "" };

function message(cause: unknown) {
  return cause instanceof Error ? cause.message : "Something went wrong. Try again.";
}

/** Connect or disconnect the company's WhatsApp number. Only administrators can change it. */
export function WhatsAppPanel({ tenantId, canManage, platform }: {
  tenantId: string | null; canManage: boolean; platform: boolean;
}) {
  const [number, setNumber] = useState<WhatsAppNumber | null>(null);
  const [form, setForm] = useState(EMPTY);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    let live = true;
    getWhatsAppNumber(tenantId)
      .then((value) => { if (live) setNumber(value); })
      .catch((cause) => { if (live) setError(message(cause)); });
    return () => { live = false; };
  }, [tenantId]);

  async function run(call: () => Promise<WhatsAppNumber | void>) {
    setBusy(true);
    setError("");
    try {
      await call();
      setForm(EMPTY);
      setNumber(await getWhatsAppNumber(tenantId));
    } catch (cause) {
      setError(message(cause));
    } finally {
      setBusy(false);
    }
  }
  function update(key: keyof typeof EMPTY) {
    return (event: React.ChangeEvent<HTMLInputElement>) =>
      setForm((value) => ({ ...value, [key]: event.target.value }));
  }

  return (
    <Panel title="WhatsApp number">
      {error && <div className="mb-3"><Notice error>{error}</Notice></div>}
      {!number ? (error ? null : <Notice>Loading WhatsApp number...</Notice>) : <>
        {!number.callback_url && <Notice>WhatsApp is not switched on for this deployment yet.</Notice>}
        {number.connected ? (
          <div className="space-y-3">
            <p className="text-sm" data-testid="whatsapp-connected">
              Connected: {number.display_phone} · Messages arrive in the Inbox.</p>
            {canManage && <button className={secondary} disabled={busy} data-testid="whatsapp-disconnect"
              onClick={() => {
                if (window.confirm("Disconnect this number? Customers will not be able to message you on WhatsApp."))
                  void run(() => disconnectWhatsAppNumber(tenantId));
              }}>Disconnect number</button>}
          </div>
        ) : canManage ? (
          <form className="mt-3 space-y-3" onSubmit={(event) => {
            event.preventDefault();
            void run(() => connectWhatsAppNumber(tenantId, {
              phone_number_id: form.phone_number_id.trim(), waba_id: form.waba_id.trim(),
              access_token: form.access_token.trim(),
            }));
          }}>
            <label className="block text-sm">Phone number ID
              <input className={field} required inputMode="numeric" pattern="[0-9]{5,32}" autoComplete="off"
                data-testid="whatsapp-phone-number-id" value={form.phone_number_id} onChange={update("phone_number_id")} />
            </label>
            <label className="block text-sm">WhatsApp Business Account ID
              <input className={field} required inputMode="numeric" pattern="[0-9]{5,32}" autoComplete="off"
                data-testid="whatsapp-waba-id" value={form.waba_id} onChange={update("waba_id")} />
            </label>
            <label className="block text-sm">Access token
              <input className={field} required type="password" minLength={20} maxLength={4096} autoComplete="off"
                data-testid="whatsapp-access-token" value={form.access_token} onChange={update("access_token")} />
            </label>
            <p className="text-sm text-ink-600">We check the number with WhatsApp before connecting it.</p>
            <button className={action} disabled={busy} data-testid="whatsapp-connect">Connect number</button>
          </form>
        ) : (
          <p className="text-sm">No WhatsApp number is connected. Ask your administrator to connect one.</p>
        )}
        {platform && number.callback_url && (
          <p className="mt-4 break-all text-sm text-ink-600">
            Paste this callback address into your Meta app: {number.callback_url}</p>
        )}
      </>}
    </Panel>
  );
}
