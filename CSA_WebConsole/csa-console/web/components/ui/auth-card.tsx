import { FourStacksLogo } from "@/components/ui/FourStacksLogo";
import { AUTH_TAGLINE } from "@/lib/auth-copy";

/**
 * Every sign-in style page (sign in, reset request, forced password change, invite and reset
 * redemption). From 1024 px: the brand panel (logo, wordmark, one tagline) beside the form. Below
 * that the panel becomes a slim banner so the logo is still there. The brand panel is the same
 * cobalt in every theme.
 */
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
    <main className="grid min-h-screen grid-rows-[auto_1fr] bg-[var(--app-bg)] lg:grid-cols-2 lg:grid-rows-1">
      <header
        data-testid="auth-banner"
        className="auth-brand flex items-center gap-3 px-5 py-3 lg:hidden"
      >
        <FourStacksLogo size={40} animated={false} />
        <span
          data-testid="auth-banner-wordmark"
          className="brand-wordmark text-xl font-bold tracking-tight"
        >
          fourstacks
        </span>
      </header>
      <aside
        data-testid="auth-panel"
        className="auth-brand hidden flex-col items-center justify-center p-12 text-center lg:flex"
      >
        <FourStacksLogo size={120} />
        <span
          data-testid="auth-wordmark"
          className="brand-wordmark mt-2 text-[26px] font-bold tracking-tight"
        >
          fourstacks
        </span>
        <p data-testid="auth-tagline" className="mt-3 text-[15px]">
          {AUTH_TAGLINE}
        </p>
      </aside>
      <section className="flex items-start justify-center px-5 py-10 lg:items-center lg:py-12">
        <div className="w-full max-w-[380px]">
          <h1
            data-testid="auth-title"
            className="text-3xl font-semibold tracking-tight"
          >
            {title}
          </h1>
          {subtitle && <p className="mt-2 text-sm text-ink-600">{subtitle}</p>}
          <div className={subtitle ? "mt-8" : "mt-6"}>{children}</div>
        </div>
      </section>
    </main>
  );
}
