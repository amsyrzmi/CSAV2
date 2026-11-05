/**
 * Pure rules for connecting a TikTok shop from the Store connections page: the one-line
 * result after TikTok sends the browser back (`?tiktok=connected|failed&reason=<code>`), the
 * plain "synced 3 min ago" line, and the check that the browser is only ever sent to TikTok's
 * own authorize page. The reason codes are the ones the console callback redirects with
 * (csa/console/tiktok_connect.py); the sentences never carry text from TikTok.
 */

/**
 * The hosts the browser may be sent to after Connect. The URL comes from our own server, but a
 * wrong one would hand the Admin to a stranger, so it is checked here as well.
 * Verify the Malaysia authorize host in Partner Center, ledger Task 21.
 */
export const TIKTOK_AUTHORIZE_HOSTS: readonly string[] = ["services.tiktokshop.com"];

export const TIKTOK_UNAVAILABLE = "TikTok Shop isn't set up on this server yet.";

const REASONS: Record<string, string> = {
  tiktok_unavailable: TIKTOK_UNAVAILABLE,
  provider_denied: "TikTok said the connection wasn't approved. Nothing was changed.",
  state_invalid: "That connection link has expired or was already used. Start again with Connect TikTok Shop.",
  multiple_shops: "This TikTok account has more than one shop. Contact support to connect the right one.",
  no_authorized_shop: "TikTok didn't share any shop with us. Connect again and choose your shop.",
  shop_in_use: "That TikTok shop is already connected. If it is yours, use Reconnect on its card.",
  insufficient_scope: "TikTok didn't give us all the access we need. Connect again and allow every permission.",
  // Covers a failed check and a different shop than the one stored for a reconnect.
  connect_failed:
    "We couldn't confirm the shop you approved. If you chose a different shop than before, connect the original one.",
};

export type ConnectResult = { tone: "good" | "bad"; text: string };

/** The one-line result for the page's query, or null when TikTok has not just sent the Admin back. */
export function connectResult(search: URLSearchParams): ConnectResult | null {
  const outcome = search.get("tiktok");
  if (outcome === "connected") {
    return { tone: "good", text: "TikTok Shop is connected. Orders and products will start syncing shortly." };
  }
  if (outcome !== "failed") return null;
  const reason = search.get("reason") ?? "";
  // A code this page does not know gets the general sentence, never the raw code.
  return { tone: "bad", text: Object.hasOwn(REASONS, reason) ? REASONS[reason] : REASONS.connect_failed };
}

/** What a failed Connect, Reconnect or Disconnect call says; other messages stay as the server gave them. */
export function connectErrorText(code: string, message: string): string {
  if (code === "tiktok_unavailable") return TIKTOK_UNAVAILABLE;
  if (code === "store_admin_required") return "Only an Admin can connect or disconnect a shop.";
  if (code === "store_not_found") return "That shop is no longer here. Refresh the page.";
  if (code === "store_changed") return "The shop changed while you were connecting it. Refresh the page and try again.";
  if (code === "not_available") return "Disconnecting isn't available on this server yet.";
  return message;
}

/**
 * "Orders synced 3 min ago", "Products synced 1 h ago", "Orders not synced yet". With no label it
 * is the sentence for one record: "Synced 3 min ago", "Not synced yet".
 */
export function syncLine(label: "Orders" | "Products" | null, at: string | null, now: Date): string {
  if (at === null) return label ? `${label} not synced yet` : "Not synced yet";
  const time = new Date(at).getTime();
  if (Number.isNaN(time)) return label ? `${label} sync time not recorded` : "Sync time not recorded";
  const minutes = Math.floor(Math.max(0, now.getTime() - time) / 60_000);
  const ago = minutes < 1 ? "just now"
    : minutes < 60 ? `${minutes} min ago`
    : minutes < 1440 ? `${Math.floor(minutes / 60)} h ago`
    : `${Math.floor(minutes / 1440)} d ago`;
  return label ? `${label} synced ${ago}` : `Synced ${ago}`;
}

/** True only for an https URL on one of the TikTok authorize hosts (no credentials, no other scheme). */
export function isAuthorizeUrl(value: unknown): value is string {
  if (typeof value !== "string") return false;
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    return false;
  }
  return url.protocol === "https:" && url.username === "" && url.password === ""
    && TIKTOK_AUTHORIZE_HOSTS.includes(url.hostname);
}

/**
 * Sends the browser to TikTok. It refuses (returns false) anything but a checked authorize URL.
 * `assign` is injectable so a test can see where the browser would go.
 */
export function navigate(url: string, assign: (url: string) => void = (to) => window.location.assign(to)): boolean {
  if (!isAuthorizeUrl(url)) return false;
  assign(url);
  return true;
}
