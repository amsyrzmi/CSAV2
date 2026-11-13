"use client";

import { useId, useRef, useState } from "react";
import { LEDGER_PROVIDERS, postFx, postPrice, type Provider } from "@/lib/billing-admin-api";
import { fullDay, fxErrorMessage, isUsdAmount, klDateTime, priceErrorMessage } from "@/lib/billing-admin-view";
import { action, field, Notice, Panel } from "../ui";

const PROVIDER_NAMES: Record<Provider, string> = { groq: "Groq", gemini: "Gemini" };
const AMOUNT_HINT = "US dollars per million tokens, above zero, at most 8 decimal places.";
const START_HINT =
  "Kuala Lumpur time. A row can only be back-dated before the earliest existing one, to fill unpriced calls; otherwise it must start today or tomorrow. A row dated today also re-prices today's earlier calls.";

/** One submit at a time; the message on screen is the outcome of the latest one. */
function useSubmit() {
  const working = useRef(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState("");
  async function run(task: () => Promise<string>, explain: (cause: unknown) => string) {
    if (working.current) return;
    working.current = true;
    setBusy(true);
    setError("");
    setSaved("");
    try {
      setSaved(await task());
    } catch (cause) {
      setError(explain(cause));
    } finally {
      working.current = false;
      setBusy(false);
    }
  }
  return { busy, error, saved, setError, setSaved, run };
}

function StartFields({ id, date, time, busy, onDate, onTime }: {
  id: string; date: string; time: string; busy: boolean; onDate: (v: string) => void; onTime: (v: string) => void;
}) {
  return (
    <fieldset aria-describedby={`${id}-start-hint`}>
      <legend className="mb-2 block text-sm">Starts</legend>
      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          <label htmlFor={`${id}-date`} className="mb-1 block text-xs text-ink-600">Date</label>
          <input id={`${id}-date`} type="date" className={field} required value={date} disabled={busy}
            onChange={(e) => onDate(e.target.value)} />
        </div>
        <div>
          <label htmlFor={`${id}-time`} className="mb-1 block text-xs text-ink-600">Time</label>
          <input id={`${id}-time`} type="time" className={field} required value={time} disabled={busy}
            onChange={(e) => onTime(e.target.value)} />
        </div>
      </div>
      <p id={`${id}-start-hint`} className="mt-2 text-sm text-ink-600">{START_HINT}</p>
    </fieldset>
  );
}

/** A model price in USD per million tokens, from a Kuala Lumpur date and time. */
export function PriceForm({ onSaved }: { onSaved?: () => void }) {
  const id = useId();
  const [provider, setProvider] = useState<Provider>("groq");
  const [model, setModel] = useState("");
  const [input, setInput] = useState("");
  const [output, setOutput] = useState("");
  const [date, setDate] = useState("");
  const [time, setTime] = useState("00:00");
  const { busy, error, saved, setError, setSaved, run } = useSubmit();

  function submit(event: React.FormEvent) {
    event.preventDefault();
    const start = klDateTime(date, time);
    const name = model.trim();
    if (!name || !isUsdAmount(input.trim()) || !isUsdAmount(output.trim()) || !start) {
      setSaved("");
      setError(priceErrorMessage({ status: 422, code: "form" }));
      return;
    }
    void run(async () => {
      await postPrice({
        provider, model: name, input_usd_per_mtok: input.trim(), output_usd_per_mtok: output.trim(), effective_from: start,
      });
      onSaved?.();
      return `Price saved for ${PROVIDER_NAMES[provider]} ${name} from ${fullDay(date)}, ${time} Kuala Lumpur time.`;
    }, priceErrorMessage);
  }

  return (
    <Panel title="Add a model price">
      <form onSubmit={submit} className="space-y-4" data-testid="billing-price-form" noValidate>
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <label htmlFor={`${id}-provider`} className="mb-2 block text-sm">Provider</label>
            <select id={`${id}-provider`} className={field} value={provider} disabled={busy}
              onChange={(e) => setProvider(e.target.value as Provider)}>
              {LEDGER_PROVIDERS.map((value) => <option key={value} value={value}>{PROVIDER_NAMES[value]}</option>)}
            </select>
          </div>
          <div>
            <label htmlFor={`${id}-model`} className="mb-2 block text-sm">Model</label>
            <input id={`${id}-model`} className={field} required maxLength={200} value={model} disabled={busy}
              aria-describedby={`${id}-model-hint`} onChange={(e) => setModel(e.target.value)} />
            <p id={`${id}-model-hint`} className="mt-2 text-sm text-ink-600">Exactly as the provider writes it, capitals included.</p>
          </div>
          <div>
            <label htmlFor={`${id}-input`} className="mb-2 block text-sm">Input price (USD)</label>
            <input id={`${id}-input`} className={field} inputMode="decimal" required value={input} disabled={busy}
              aria-describedby={`${id}-amount-hint`} onChange={(e) => setInput(e.target.value)} />
          </div>
          <div>
            <label htmlFor={`${id}-output`} className="mb-2 block text-sm">Output price (USD)</label>
            <input id={`${id}-output`} className={field} inputMode="decimal" required value={output} disabled={busy}
              aria-describedby={`${id}-amount-hint`} onChange={(e) => setOutput(e.target.value)} />
          </div>
        </div>
        <p id={`${id}-amount-hint`} className="text-sm text-ink-600">{AMOUNT_HINT}</p>
        <StartFields id={id} date={date} time={time} busy={busy} onDate={setDate} onTime={setTime} />
        {error && <Notice error testId="billing-price-error">{error}</Notice>}
        {saved && <Notice testId="billing-price-saved">{saved}</Notice>}
        <button type="submit" className={action} disabled={busy}>{busy ? "Saving…" : "Save price"}</button>
      </form>
    </Panel>
  );
}

/** A USD to MYR rate from a Kuala Lumpur date and time. */
export function FxForm({ onSaved }: { onSaved?: () => void }) {
  const id = useId();
  const [rate, setRate] = useState("");
  const [date, setDate] = useState("");
  const [time, setTime] = useState("00:00");
  const { busy, error, saved, setError, setSaved, run } = useSubmit();

  function submit(event: React.FormEvent) {
    event.preventDefault();
    const start = klDateTime(date, time);
    // The rate shares the USD amount's shape; the server also caps it below 1,000.
    if (!isUsdAmount(rate.trim()) || !start) {
      setSaved("");
      setError(fxErrorMessage({ status: 422, code: "form" }));
      return;
    }
    void run(async () => {
      await postFx({ usd_to_myr: rate.trim(), effective_from: start });
      onSaved?.();
      return `Rate saved: 1 USD = ${rate.trim()} MYR from ${fullDay(date)}, ${time} Kuala Lumpur time.`;
    }, fxErrorMessage);
  }

  return (
    <Panel title="Add a USD to MYR rate">
      <form onSubmit={submit} className="space-y-4" data-testid="billing-fx-form" noValidate>
        <div>
          <label htmlFor={`${id}-rate`} className="mb-2 block text-sm">MYR for 1 USD</label>
          <input id={`${id}-rate`} className={`${field} max-w-xs`} inputMode="decimal" required value={rate} disabled={busy}
            aria-describedby={`${id}-rate-hint`} onChange={(e) => setRate(e.target.value)} />
          <p id={`${id}-rate-hint`} className="mt-2 text-sm text-ink-600">Above zero, at most 8 decimal places.</p>
        </div>
        <StartFields id={id} date={date} time={time} busy={busy} onDate={setDate} onTime={setTime} />
        {error && <Notice error testId="billing-fx-error">{error}</Notice>}
        {saved && <Notice testId="billing-fx-saved">{saved}</Notice>}
        <button type="submit" className={action} disabled={busy}>{busy ? "Saving…" : "Save rate"}</button>
      </form>
    </Panel>
  );
}
