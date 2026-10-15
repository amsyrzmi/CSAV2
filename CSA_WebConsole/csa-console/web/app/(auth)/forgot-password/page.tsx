"use client";
import Link from "next/link";
import { useState } from "react";
import { identity } from "@/lib/identity-api";
import { AuthCard } from "@/components/ui/auth-card";
import { action, field, Notice } from "@/components/console/ui";

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await identity.requestReset(email);
      setSent(true);
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <AuthCard
      title="Reset your password"
      subtitle="A workspace administrator can issue a secure reset link."
    >
      {sent ? (
        <Notice>
          Your request has been recorded. Contact your administrator for a reset
          link.
        </Notice>
      ) : (
        <form onSubmit={submit} className="space-y-5">
          <div>
            <label htmlFor="email" className="mb-2 block text-sm">
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
          {error && <Notice error>{error}</Notice>}
          <button className={action + " w-full"} disabled={busy}>
            {busy ? "Submitting…" : "Request reset"}
          </button>
        </form>
      )}
      <Link
        href="/login"
        className="mt-5 block text-center text-sm text-brand-600"
      >
        Back to sign in
      </Link>
    </AuthCard>
  );
}
