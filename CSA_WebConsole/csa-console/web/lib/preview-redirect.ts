/** The old private-chat pages only redirect to the customer preview section on Services. */
type Query = { [key: string]: string | string[] | undefined };

/**
 * `base` is the Services page the caller belongs to. Every incoming query parameter is kept in
 * order (a repeated one stays repeated), and `section=preview` replaces any `section` it had.
 */
export function previewHref(base: "/services" | "/portal/services", query: Query): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (key === "section" || value === undefined) continue;
    for (const item of Array.isArray(value) ? value : [value]) params.append(key, item);
  }
  params.set("section", "preview");
  return `${base}?${params}`;
}
