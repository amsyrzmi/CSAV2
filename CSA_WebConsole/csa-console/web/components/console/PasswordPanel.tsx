"use client";
import { useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { identity } from "@/lib/identity-api";
import { PasswordInput } from "@/components/ui/password-input";
import { action, Notice, Panel } from "@/components/console/ui";

const SUCCESS = "Password updated. Other devices have been signed out.";

/** Change the signed-in person's own password without leaving the dashboard shell. */
export function PasswordPanel() {
  const router = useRouter();
  const [current, setCurrent] = useState("");
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState(false);
  // State lags a render behind a second click in the same task; a ref does not.
  const sending = useRef(false);

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
      setError((cause as Error).message);
    } finally {
      sending.current = false;
      setBusy(false);
    }
  }

  return (
    <div className="max-w-xl">
      <Panel title="Change your password">
        <form onSubmit={submit} className="space-y-5">
          <div>
            <label htmlFor="password-current" className="mb-2 block text-sm">
              Current password
            </label>
            <PasswordInput
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
            <PasswordInput
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
          </div>
          <div>
            <label htmlFor="password-confirm" className="mb-2 block text-sm">
              Confirm new password
            </label>
            <PasswordInput
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
          {done && <Notice testId="password-success">{SUCCESS}</Notice>}
          <button data-testid="password-submit" disabled={busy} className={action}>
            {busy ? "Saving…" : "Update password"}
          </button>
        </form>
      </Panel>
    </div>
  );
}
