/** The old private-chat pages only redirect to the customer preview section on Services. */
type Query = { [key: string]: string | string[] | undefined };

/**
 * `base` is the Services page the caller belongs to. Every incoming query parameter is kept in
 * order (a repeated one stays repeated, except `client_id`, which must be a single non-blank value), and `section=preview` replaces any `section` it had.
 */
export function previewHref(base: "/services" | "/portal/services", query: Query): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (key === "section" || value === undefined) continue;
    if (key === "client_id") {
      // The company is one trimmed, non-empty value: a repeated or blank one is ambiguous, so it is dropped.
      if (typeof value === "string" && value.trim()) params.append(key, value.trim());
      continue;
    }
    for (const item of Array.isArray(value) ? value : [value]) params.append(key, item);
  }
  params.set("section", "preview");
  return `${base}?${params}`;
}
