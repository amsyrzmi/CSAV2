"use client";
import { useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { ApiError } from "@/lib/api";
import { identity } from "@/lib/identity-api";
import { passwordErrorText } from "@/lib/password-messages";
import { checkRules, rulesPass } from "@/lib/password-rules";
import { PasswordField } from "@/components/console/PasswordField";
import { RuleChecklist, StrengthHint } from "@/components/console/PasswordFeedback";
import { action, Notice, Panel } from "@/components/console/ui";

const SUCCESS = "Password updated. Other devices have been signed out.";
const SUCCESS_NOTE =
  "You stay signed in here. Any other devices that used your old password must sign in again. Reset and invite links you had not used have stopped working.";

/** Change the signed-in person's own password without leaving the dashboard shell. */
export function PasswordPanel({ email }: { email: string }) {
  const router = useRouter();
  const [current, setCurrent] = useState("");
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState(false);
  // State lags a render behind a second click in the same task; a ref does not.
  const sending = useRef(false);
  const rules = checkRules(current, password, confirmation);
  const ready = rulesPass(rules);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (sending.current) return;
    setError("");
    setDone(false);
    if (password !== confirmation) {
      setError("The new passwords do not match.");
      return;
    }
    sending.current = true;
    setBusy(true);
    try {
      await identity.changePassword(current, password);
      // The server rotates the session cookie; refresh so the shell reads the new one.
      router.refresh();
      setCurrent("");
      setPassword("");
      setConfirmation("");
      setDone(true);
    } catch (cause) {
      setError(passwordErrorText((cause as Error).message, cause instanceof ApiError ? cause.status : undefined));
    } finally {
      sending.current = false;
      setBusy(false);
    }
  }

  return (
    <div className="max-w-xl">
      <Panel title="Change your password">
        <form onSubmit={submit} className="space-y-5">
          {/* Lets a password manager file the new password under the right account. Not for people. */}
          <input
            type="text"
            name="username"
            autoComplete="username"
            readOnly
            tabIndex={-1}
            aria-hidden="true"
            value={email}
            className="sr-only"
          />
          <div>
            <label htmlFor="password-current" className="mb-2 block text-sm">
              Current password
            </label>
            <PasswordField
              fieldName="current password"
              id="password-current"
              data-testid="password-current"
              autoComplete="current-password"
              required
              value={current}
              onChange={(e) => setCurrent(e.target.value)}
            />
          </div>
          <div>
            <label htmlFor="password-new" className="mb-2 block text-sm">
              New password
            </label>
            <PasswordField
              fieldName="new password"
              id="password-new"
              data-testid="password-new"
              autoComplete="new-password"
              minLength={12}
              maxLength={1024}
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
            <p className="mt-2 text-xs text-ink-500">At least 12 characters.</p>
            <StrengthHint password={password} />
            <RuleChecklist rules={rules} typed={password !== "" || confirmation !== ""} />
          </div>
          <div>
            <label htmlFor="password-confirm" className="mb-2 block text-sm">
              Confirm new password
            </label>
            <PasswordField
              fieldName="confirmation"
              id="password-confirm"
              data-testid="password-confirm"
              autoComplete="new-password"
              minLength={12}
              maxLength={1024}
              required
              value={confirmation}
              onChange={(e) => setConfirmation(e.target.value)}
            />
          </div>
          {error && (
            <Notice error testId="password-error">
              {error}
            </Notice>
          )}
          {done && (
            <div className="space-y-2">
              <Notice testId="password-success">{SUCCESS}</Notice>
              <p data-testid="password-success-note" className="text-sm text-[color:var(--fs-text-secondary)]">
                {SUCCESS_NOTE}
              </p>
            </div>
          )}
          <button
            data-testid="password-submit"
            disabled={busy || !ready}
            aria-describedby={ready ? undefined : "password-save-hint"}
            className={action}
          >
            {busy ? "Saving…" : "Update password"}
          </button>
          {!ready && (
            <p id="password-save-hint" className="text-xs text-[color:var(--fs-text-secondary)]">
              Update password turns on when all three checks are done.
            </p>
          )}
        </form>
      </Panel>
    </div>
  );
}
