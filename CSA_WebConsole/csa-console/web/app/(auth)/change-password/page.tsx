"use client";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { identity } from "@/lib/identity-api";
import { homeFor } from "@/lib/identity-contracts";
import { AuthCard } from "@/components/ui/auth-card";
import { PasswordInput } from "@/components/ui/password-input";
import { action, Notice } from "@/components/console/ui";

export default function ChangePasswordPage() {
  const router = useRouter();
  const [current, setCurrent] = useState("");
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setError("");
    if (password !== confirmation) {
      setError("The new passwords do not match.");
      return;
    }
    setBusy(true);
    try {
      const me = await identity.changePassword(current, password);
      router.replace(homeFor(me));
      router.refresh();
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <AuthCard
      title="Change your password"
      subtitle="Choose a new password of at least 12 characters."
    >
      <form onSubmit={submit} className="space-y-5">
        <div>
          <label htmlFor="current" className="mb-2 block text-sm">
            Current password
          </label>
          <PasswordInput
            id="current"
            autoComplete="current-password"
            required
            value={current}
            onChange={(e) => setCurrent(e.target.value)}
          />
        </div>
        <div>
          <label htmlFor="new" className="mb-2 block text-sm">
            New password
          </label>
          <PasswordInput
            id="new"
            autoComplete="new-password"
            minLength={12}
            maxLength={1024}
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </div>
        <div>
          <label htmlFor="confirm" className="mb-2 block text-sm">
            Confirm new password
          </label>
          <PasswordInput
            id="confirm"
            autoComplete="new-password"
            minLength={12}
            maxLength={1024}
            required
            value={confirmation}
            onChange={(e) => setConfirmation(e.target.value)}
          />
        </div>
        {error && <Notice error>{error}</Notice>}
        <button disabled={busy} className={action + " w-full"}>
          {busy ? "Saving…" : "Save password"}
        </button>
      </form>
    </AuthCard>
  );
}
