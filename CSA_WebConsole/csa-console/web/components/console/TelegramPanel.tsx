"use client";

import { useEffect, useState } from "react";
import { ApiError } from "@/lib/api";
import {
  checkTelegram,
  connectTelegram,
  disconnectTelegram,
  getTelegram,
  setTelegramStaffChat,
  type TelegramCheck,
  type TelegramStatus,
} from "@/lib/channels-api";
import ConfirmAction from "./email/ConfirmAction";
import { action, field, Notice, Panel } from "./ui";

const EMPTY = { token: "", chat: "" };

function message(cause: unknown) {
  return cause instanceof Error ? cause.message : "Something went wrong. Try again.";
}

function staffChatLine(status: TelegramStatus) {
  if (!status.staff_chat) return "No staff chat is set, so escalations and approval requests reach nobody.";
  return status.staff_chat.verified
    ? "Staff chat is set. Escalations and approval requests go there."
    : "A staff chat is saved but not verified yet.";
}

/**
 * Set up or read the company's Telegram bot. Only the platform team can change it; the company's
 * own members see its status. The token is typed here once and never shown again.
 */
export function TelegramPanel({ tenantId, platform }: { tenantId: string | null; platform: boolean }) {
  const [status, setStatus] = useState<TelegramStatus | null>(null);
  const [form, setForm] = useState(EMPTY);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [needsReplace, setNeedsReplace] = useState(false);
  const [check, setCheck] = useState<TelegramCheck | null>(null);
  useEffect(() => {
    let live = true;
    getTelegram(tenantId)
      .then((value) => { if (live) setStatus(value); })
      .catch((cause) => { if (live) setError(message(cause)); });
    return () => { live = false; };
  }, [tenantId]);

  async function run(call: () => Promise<unknown>, { keepToken = false } = {}) {
    setBusy(true);
    setError("");
    setNeedsReplace(false);
    try {
      await call();
      setForm(keepToken ? (value) => ({ ...value, chat: "" }) : EMPTY);
    } catch (cause) {
      if (cause instanceof ApiError && cause.code === "telegram_webhook_in_use") setNeedsReplace(true);
      setError(message(cause));
    } finally {
      try { setStatus(await getTelegram(tenantId)); } catch { /* the error above already says what failed */ }
      setBusy(false);
    }
  }
  const connect = (replace: boolean) => run(() => connectTelegram(tenantId, {
    bot_token: form.token.trim(), staff_chat_id: form.chat.trim() || undefined, replace_webhook: replace,
  }));
  function update(key: keyof typeof EMPTY) {
    return (event: React.ChangeEvent<HTMLInputElement>) =>
      setForm((value) => ({ ...value, [key]: event.target.value }));
  }

  return (
    <Panel title="Telegram bot">
      {error && <div className="mb-3"><Notice error>{error}</Notice></div>}
      {!status ? (error ? null : <Notice>Loading Telegram bot...</Notice>) : <>
        {status.connected ? (
          <p className="text-sm" data-testid="telegram-connected">
            Connected: @{status.bot_username} · Customer messages arrive in the Inbox.</p>
        ) : (
          <p className="text-sm">{status.bot_id
            ? "The bot is saved but its webhook is not registered, so no messages arrive."
            : "No Telegram bot is connected."}</p>
        )}
        <p className="mt-2 text-sm text-ink-600" data-testid="telegram-staff-chat-line">{staffChatLine(status)}</p>
        {!platform ? (
          <p className="mt-3 text-sm text-ink-600">Ask the platform team to change this.</p>
        ) : !status.hooks_ready ? (
          <div className="mt-3"><Notice>Telegram isn&apos;t available yet: this deployment has no public hooks address.</Notice></div>
        ) : <>
          <form className="mt-4 space-y-3" onSubmit={(event) => { event.preventDefault(); void connect(false); }}>
            <label className="block text-sm">{status.bot_id ? "New bot token (same bot)" : "Bot token from BotFather"}
              <input className={field} required type="password" minLength={20} maxLength={200} autoComplete="off"
                data-testid="telegram-token" value={form.token} onChange={update("token")} />
            </label>
            {!status.bot_id && (
              <label className="block text-sm">Staff chat ID (optional now)
                <input className={field} inputMode="numeric" pattern="-?[0-9]{1,20}" autoComplete="off"
                  data-testid="telegram-staff-chat" value={form.chat} onChange={update("chat")} />
              </label>
            )}
            <p className="text-sm text-ink-600">We check the token with Telegram before saving it.</p>
            <button className={action} disabled={busy} data-testid="telegram-connect">
              {status.bot_id ? "Replace token" : "Connect bot"}</button>
          </form>
          {needsReplace && (
            <div className="mt-3 space-y-2" data-testid="telegram-replace-webhook">
              <p className="text-sm">This bot is wired to something else. Replace it?</p>
              <button className={action} disabled={busy} onClick={() => void connect(true)}>Replace webhook</button>
            </div>
          )}
          {status.bot_id && (
            <form className="mt-4 space-y-3" onSubmit={(event) => {
              event.preventDefault();
              void run(() => setTelegramStaffChat(tenantId, form.chat.trim()), { keepToken: true });
            }}>
              <label className="block text-sm">Staff chat ID
                <input className={field} required inputMode="numeric" pattern="-?[0-9]{1,20}" autoComplete="off"
                  data-testid="telegram-staff-chat" value={form.chat} onChange={update("chat")} />
              </label>
              <p className="text-sm text-ink-600">
                Add the bot to the staff group first, then paste the group&apos;s ID. We send a test message to confirm.</p>
              <button className={action} disabled={busy} data-testid="telegram-test-chat">Send test message</button>
            </form>
          )}
          {status.bot_id && (
            <div className="mt-4 space-y-3">
              <button className={action} disabled={busy} data-testid="telegram-check"
                onClick={() => void (async () => {
                  setBusy(true); setError("");
                  try { setCheck(await checkTelegram(tenantId)); } catch (cause) { setError(message(cause)); }
                  finally { setBusy(false); }
                })()}>Check webhook</button>
              {check && (
                <p className="text-sm" data-testid="telegram-check-result">
                  Webhook {check.webhook_url_matches ? "matches" : "does not match"} · {check.pending_update_count} waiting
                  {check.last_error ? ` · Last error: ${check.last_error}` : ""}</p>
              )}
              <ConfirmAction label="Disconnect bot" testId="telegram-disconnect" disabled={busy}
                question="Disconnect this bot? Customers will not be able to message this company on Telegram, and staff alerts stop."
                onConfirm={() => void run(() => disconnectTelegram(tenantId))} />
            </div>
          )}
        </>}
      </>}
    </Panel>
  );
}
