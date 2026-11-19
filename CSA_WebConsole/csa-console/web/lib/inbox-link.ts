/**
 * The Inbox deep link, `?conversation=<id>`: which value opens a thread, and the address once the
 * screen has used it. Pure (no runtime alias imports) so a node test can load it.
 */

const UUID_SHAPE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/**
 * The id a link names, lower-cased, or "" when the value is not exactly one canonical UUID. A page
 * `searchParams` value is a string, an array (a repeated parameter) or absent; only a string passes.
 */
export function linkedConversation(value: unknown): string {
  return typeof value === "string" && UUID_SHAPE.test(value) ? value.toLowerCase() : "";
}

/** `pathname` with every parameter but `conversation`, and no trailing `?` when none is left. */
export function pathWithoutConversation(pathname: string, params: URLSearchParams): string {
  const rest = new URLSearchParams(params);
  rest.delete("conversation");
  const query = rest.toString();
  return query ? `${pathname}?${query}` : pathname;
}
