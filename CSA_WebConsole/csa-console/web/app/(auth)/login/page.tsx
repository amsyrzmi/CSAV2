"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { identity } from "@/lib/identity-api";
import { homeFor } from "@/lib/identity-contracts";
import { AuthCard } from "@/components/ui/auth-card";
import { PasswordInput } from "@/components/ui/password-input";
import { action, field, Notice } from "@/components/console/ui";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const me = await identity.login(email, password);
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
      title="Sign in"
      subtitle="Enter your account details to continue."
    >
      <form onSubmit={submit} className="space-y-5">
        <div>
          <label className="mb-2 block text-sm" htmlFor="email">
            Email
          </label>
          <input
            id="email"
            className={field}
            type="email"
            autoComplete="username"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </div>
        <div>
          <label className="mb-2 block text-sm" htmlFor="password">
            Password
          </label>
          <PasswordInput
            id="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </div>
        {error && <Notice error>{error}</Notice>}
        <button className={action + " w-full"} disabled={busy}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
        <Link
          href="/forgot-password"
          className="block text-center text-sm text-brand-600 hover:underline"
        >
          Forgot password?
        </Link>
      </form>
    </AuthCard>
  );
}
