// Shared console control classes (UI_GUIDE sections 4, 5, 6 and 9): the theme's control
// radius (6 px, square in Bureau), 40 px controls that grow to a 44 px touch target on coarse pointers,
// the --fs-focus ring, and 150 ms hover/press feedback on --fs-ease-out.
const motion =
  "transition-[background-color,border-color,box-shadow,color,scale,transform] duration-150 ease-(--fs-ease-out)";
const ring =
  "focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--fs-focus)]";
const button = `inline-flex min-h-10 items-center justify-center gap-2 rounded-[var(--fs-radius-control)] px-4 py-2 text-sm font-medium pointer-coarse:min-h-11 ${motion} ${ring} active:scale-[0.98] disabled:cursor-not-allowed disabled:border-[var(--fs-border)] disabled:bg-[var(--fs-disabled-bg)] disabled:text-[var(--fs-disabled-text)] disabled:active:scale-100`;

export const field = `h-10 w-full rounded-[var(--fs-radius-control)] border border-ink-300 bg-[var(--fs-surface)] px-3 text-sm text-ink-900 pointer-coarse:h-11 placeholder:text-ink-500 hover:border-ink-500 focus:border-brand-600 disabled:cursor-not-allowed disabled:bg-[var(--fs-disabled-bg)] disabled:text-[var(--fs-disabled-text)] ${motion} ${ring}`;
export const action = `${button} bg-brand-600 text-[color:var(--fs-on-primary)] hover:bg-brand-700`;
export const secondary = `${button} border border-ink-300 bg-[var(--fs-surface)] text-ink-900 hover:border-brand-600 hover:bg-brand-50`;
/** `error` for a failure; `warning` for something the reader must act on that is not a failure. */
export function Notice({
  children,
  error = false,
  warning = false,
  testId,
}: {
  children: React.ReactNode;
  error?: boolean;
  warning?: boolean;
  testId?: string;
}) {
  const tone = error
    ? "border-[var(--fs-danger)]/30 bg-[var(--fs-danger-bg)] text-[var(--fs-danger)]"
    : warning
      ? "border-[var(--fs-warning)]/30 bg-[var(--fs-warning-bg)] text-[var(--fs-warning)]"
      : "border-brand-200 bg-brand-50 text-ink-900";
  return (
    <p
      role={error || warning ? "alert" : "status"}
      data-testid={testId}
      className={`rounded-[var(--fs-radius-control)] border p-3 text-sm ${tone}`}
    >
      {children}
    </p>
  );
}
export function Heading({
  title,
  description,
}: {
  title: string;
  description: string;
}) {
  return (
    <div className="mb-7">
      <h1 className="page-title font-(family-name:--fs-font-heading) text-(length:--fs-text-page) font-semibold">{title}</h1>
      <p className="page-description mt-2">{description}</p>
    </div>
  );
}
export function Panel({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="card rounded-[14px] p-5 sm:p-6">
      <h2 className="mb-5 font-(family-name:--fs-font-heading) text-base font-semibold">{title}</h2>
      {children}
    </section>
  );
}
