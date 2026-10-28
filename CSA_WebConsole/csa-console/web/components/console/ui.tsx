// Shared console control classes (UI_GUIDE sections 4, 5, 6 and 9): 6 px control
// radius, 40 px controls that grow to a 44 px touch target on coarse pointers,
// the --fs-focus ring, and 150 ms hover/press feedback on --fs-ease-out.
const motion =
  "transition-[background-color,border-color,box-shadow,color,transform] duration-150 ease-(--fs-ease-out)";
const ring =
  "focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--fs-focus)]";
const button = `inline-flex min-h-10 items-center justify-center gap-2 rounded-[6px] px-4 py-2 text-sm font-medium pointer-coarse:min-h-11 ${motion} ${ring} active:scale-[0.98] disabled:cursor-not-allowed disabled:border-[var(--fs-border)] disabled:bg-[var(--fs-disabled-bg)] disabled:text-[var(--fs-disabled-text)] disabled:active:scale-100`;

export const field = `h-10 w-full rounded-[6px] border border-ink-300 bg-white px-3 text-sm text-ink-900 pointer-coarse:h-11 placeholder:text-ink-500 hover:border-ink-500 focus:border-brand-600 disabled:cursor-not-allowed disabled:bg-[var(--fs-disabled-bg)] disabled:text-[var(--fs-disabled-text)] ${motion} ${ring}`;
export const action = `${button} bg-brand-600 text-white hover:bg-brand-700`;
export const secondary = `${button} border border-ink-300 bg-white text-ink-900 hover:border-brand-600 hover:bg-brand-50`;
export function Notice({
  children,
  error = false,
}: {
  children: React.ReactNode;
  error?: boolean;
}) {
  return (
    <p
      role={error ? "alert" : "status"}
      className={`rounded-[6px] border p-3 text-sm ${error ? "border-[var(--fs-danger)]/30 bg-[var(--fs-danger-bg)] text-[var(--fs-danger)]" : "border-brand-200 bg-brand-50 text-ink-900"}`}
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
      <h1 className="page-title">{title}</h1>
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
