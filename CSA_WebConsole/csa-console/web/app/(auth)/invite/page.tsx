"use client";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { identity } from "@/lib/identity-api";
import { homeFor } from "@/lib/identity-contracts";
import type { TokenInfo } from "@/lib/identity-contracts";
import { AuthCard } from "@/components/ui/auth-card";
import { PasswordInput } from "@/components/ui/password-input";
import { action, field, Notice } from "@/components/console/ui";

export default function InvitePage() {
  const router = useRouter();
  const [token, setToken] = useState("");
  const [info, setInfo] = useState<TokenInfo | null>(null);
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(true);
  useEffect(() => {
    let live = true;
    Promise.resolve().then(async () => {
      if (!live) return;
      const value =
        new URLSearchParams(window.location.hash.slice(1)).get("token") ?? "";
      window.history.replaceState(null, "", "/invite");
      setToken(value);
      if (value) {
        try {
          const result = await identity.inspect(value);
          if (live) {
            setInfo(result);
            setName(result.display_name ?? "");
          }
        } catch (cause) {
          if (live) setError((cause as Error).message);
        }
      }
      if (live) setBusy(false);
    });
    return () => {
      live = false;
    };
  }, []);
  async function inspect(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const result = await identity.inspect(token);
      setInfo(result);
      setName(result.display_name ?? "");
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function accept(event: React.FormEvent) {
    event.preventDefault();
    setError("");
    if (password !== confirm) {
      setError("The passwords do not match.");
      return;
    }
    setBusy(true);
    try {
      const me = await identity.accept(
        token,
        password,
        info?.kind === "invite" ? name : undefined,
      );
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
      title={
        info?.kind === "reset"
          ? "Reset your password"
          : "Accept your invitation"
      }
      subtitle={
        info
          ? `Account: ${info.email}`
          : "Use the secure link provided by your administrator."
      }
    >
      {!info ? (
        <form onSubmit={inspect} className="space-y-5">
          <div>
            <label htmlFor="token" className="mb-2 block text-sm">
              Invitation or reset code
            </label>
            <input
              id="token"
              className={field}
              type="password"
              autoComplete="off"
              required
              value={token}
              onChange={(e) => setToken(e.target.value)}
            />
          </div>
          {error && <Notice error>{error}</Notice>}
          <button disabled={busy} className={action + " w-full"}>
            {busy ? "Checking…" : "Check invitation"}
          </button>
        </form>
      ) : (
        <form onSubmit={accept} className="space-y-5">
          {info.kind === "invite" && (
            <div>
              <label htmlFor="name" className="mb-2 block text-sm">
                Display name
              </label>
              <input
                id="name"
                className={field}
                autoComplete="name"
                maxLength={120}
                required
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </div>
          )}
          <div>
            <label htmlFor="password" className="mb-2 block text-sm">
              New password
            </label>
            <PasswordInput
              id="password"
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
              Confirm password
            </label>
            <PasswordInput
              id="confirm"
              autoComplete="new-password"
              minLength={12}
              maxLength={1024}
              required
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
            />
          </div>
          {error && <Notice error>{error}</Notice>}
          <button disabled={busy} className={action + " w-full"}>
            {busy
              ? "Saving…"
              : info.kind === "reset"
                ? "Set password"
                : "Join workspace"}
          </button>
        </form>
      )}
    </AuthCard>
  );
}
