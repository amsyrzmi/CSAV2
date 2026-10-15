export const field =
  "h-11 w-full rounded-md border border-ink-300 bg-white px-3 text-sm outline-none focus:border-brand-600";
export const action =
  "inline-flex min-h-11 items-center justify-center gap-2 rounded-md bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700 disabled:opacity-50";
export const secondary =
  "inline-flex min-h-11 items-center justify-center rounded-md border border-ink-300 bg-white px-4 py-2 text-sm font-medium hover:bg-ink-50 disabled:opacity-50";
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
      className={`rounded-md border p-3 text-sm ${error ? "border-red-200 bg-red-50 text-red-800" : "border-brand-200 bg-brand-50 text-ink-700"}`}
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
    <section className="card p-5 sm:p-6">
      <h2 className="mb-5 text-base font-semibold">{title}</h2>
      {children}
    </section>
  );
}
