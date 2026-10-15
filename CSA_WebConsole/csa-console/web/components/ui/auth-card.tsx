import Image from "next/image";
import { KeyRound, ShieldCheck, Users } from "lucide-react";

export function AuthCard({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle?: string;
  children: React.ReactNode;
}) {
  return (
    <main className="grid min-h-screen bg-[var(--app-bg)] lg:grid-cols-2">
      <aside className="hidden flex-col justify-between border-r border-ink-200 bg-brand-50 p-12 lg:flex">
        <div className="flex items-center gap-3">
          <Image
            src="/brand/fourstacks-mark.svg"
            alt=""
            width={40}
            height={40}
          />
          <span className="brand-wordmark text-xl font-semibold">
            fourstacks
          </span>
        </div>
        <div className="max-w-md">
          <p className="eyebrow mb-4">CSA Console</p>
          <h2 className="text-4xl font-semibold leading-tight tracking-tight">
            A clear view of your workspace.
          </h2>
          <p className="mt-5 text-ink-600">
            Secure access for your team, with permissions that follow each
            tenant.
          </p>
          <ul className="mt-10 space-y-5">
            {[
              {
                icon: KeyRound,
                title: "Your identity, verified",
                text: "Personal sessions and password controls.",
              },
              {
                icon: Users,
                title: "The right level of access",
                text: "Explicit tenant memberships and team roles.",
              },
              {
                icon: ShieldCheck,
                title: "Accountable administration",
                text: "Tenant and membership changes are audited.",
              },
            ].map(({ icon: Icon, title, text }) => (
              <li key={title} className="flex gap-3">
                <Icon size={20} className="mt-1 shrink-0 text-brand-600" />
                <span>
                  <strong className="block text-sm">{title}</strong>
                  <span className="text-sm text-ink-600">{text}</span>
                </span>
              </li>
            ))}
          </ul>
        </div>
        <p className="text-xs text-ink-500">
          fourstacks · Restricted workspace access
        </p>
      </aside>
      <section className="flex items-center justify-center px-5 py-12">
        <div className="w-full max-w-[380px]">
          <div className="mb-10 flex items-center gap-3 lg:hidden">
            <Image
              src="/brand/fourstacks-mark.svg"
              alt=""
              width={32}
              height={32}
            />
            <span className="brand-wordmark text-lg font-semibold">
              fourstacks
            </span>
          </div>
          <h1 className="text-3xl font-semibold tracking-tight">{title}</h1>
          <p className="mt-2 text-sm text-ink-600">
            {subtitle ?? "Secure access to your workspace."}
          </p>
          <div className="mt-8">{children}</div>
        </div>
      </section>
    </main>
  );
}
