/**
 * The time zones a quiet-hours window can use. The server takes IANA names only, so the page
 * offers a fixed list and never a free text box. The browser's own list is used when it has one;
 * the short fallback (and these three always) cover the zones the company is most likely to use.
 */
const FALLBACK = ["Asia/Kuala_Lumpur", "Asia/Singapore", "UTC"];

/** The browser's time zone, or UTC when it cannot say. */
export function browserZone(): string {
  try {
    return new Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

function browserZones(): string[] {
  try {
    const list = (Intl as unknown as { supportedValuesOf?: (key: string) => string[] }).supportedValuesOf;
    return typeof list === "function" ? list("timeZone") : [];
  } catch {
    return [];
  }
}

/**
 * The zones to choose from, sorted. `current` (a window already saved) is always in the list,
 * so an existing value is shown as it is stored even when the browser does not list it.
 */
export function zoneChoices(current?: string): string[] {
  const all = new Set<string>([...FALLBACK, ...browserZones()]);
  if (current) all.add(current);
  return [...all].sort((left, right) => left.localeCompare(right));
}
