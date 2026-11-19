import { api, del, post } from "./api";
import { settingsPath } from "./inbox-api";
import type { InboxSettings } from "./inbox-api";

/**
 * Mirrors of the schemas in csa/console/notification_targets.py. Where alerts go (the company's
 * Telegram staff chat and email targets), and one person's own email preferences. All routes sit
 * under `/console/v1/notifications`; a platform admin names the company with `client_id`
 * (`tenantId`), a tenant member passes null and the session names the company.
 */

export type NotificationPriority = "low" | "normal" | "high" | "urgent";
export type NotificationLanguage = "en" | "ms";

/** A daily window in which only urgent alerts are sent; it may cross midnight. */
export interface QuietHours {
  start: string;
  end: string;
  tz: string;
}

export interface LastFailure {
  /** ISO time. */
  at: string;
  attempts: number;
}

export interface NotificationTarget {
  id: string;
  channel: "telegram" | "email";
  /** Never the full address: `a***@domain`, or the last four characters of a chat id. */
  address_masked: string;
  events: string[];
  min_priority: NotificationPriority;
  quiet_hours: QuietHours | null;
  language: NotificationLanguage;
  is_enabled: boolean;
  verified: boolean;
  version: number;
  last_failure: LastFailure | null;
}

export interface TargetsResponse {
  targets: NotificationTarget[];
  /** False while the mail relay is off: adding, testing and turning email on are refused (503). */
  email_available: boolean;
  events_offered: string[];
}

/** A target's settings, sent as a whole. `version` is the one last read (409 when stale). */
export interface TargetSettingsIn {
  events: string[];
  min_priority: NotificationPriority;
  quiet_hours: QuietHours | null;
  language: NotificationLanguage;
  is_enabled: boolean;
  version: number;
}

/** A new email target; what is left out takes the server's defaults. The address is trimmed and lowercased by the caller. */
export interface EmailTargetIn {
  address: string;
  events?: string[];
  min_priority?: NotificationPriority;
  quiet_hours?: QuietHours | null;
  language?: NotificationLanguage;
}

/** The signed-in member's own email preferences; a platform admin has none (404). */
export interface Preferences {
  email_enabled: boolean;
  kinds: string[];
  min_priority: NotificationPriority;
}

const BASE = "/console/v1/notifications";

/** `tenantId` is null for a tenant member and the chosen company for a platform admin. */
export function notificationsPath(tenantId: string | null, rest = ""): string {
  const query = new URLSearchParams();
  if (tenantId) query.set("client_id", tenantId);
  return `${BASE}${rest}${query.size ? `?${query}` : ""}`;
}

const id = (value: string) => `/${encodeURIComponent(value)}`;

/** Any member may read the targets. */
export function getTargets(tenantId: string | null, signal?: AbortSignal): Promise<TargetsResponse> {
  return api(notificationsPath(tenantId, "/targets"), { signal });
}

/** Admins only. 409 `version_conflict` unless `body.version` is the one read; saving clears the failure flag. */
export function saveTarget(
  tenantId: string | null,
  targetId: string,
  body: TargetSettingsIn,
): Promise<NotificationTarget> {
  return api(notificationsPath(tenantId, `/targets/${encodeURIComponent(targetId)}`), {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

/** Admins only. Sends a test mail first; 502 `email_test_failed` saves nothing. */
export function addEmailTarget(tenantId: string | null, body: EmailTargetIn): Promise<NotificationTarget> {
  return post(notificationsPath(tenantId, "/targets/email"), body);
}

/** Admins only. Sends the test mail again; delivered, it verifies the target. Telegram is 400 `test_not_supported`. */
export function testTarget(tenantId: string | null, targetId: string): Promise<NotificationTarget> {
  return post(notificationsPath(tenantId, `/targets${id(targetId)}/test`), {});
}

/** Admins only. Removes an email target (204); the Telegram chat is changed on Channels, never here. */
export function deleteTarget(tenantId: string | null, targetId: string): Promise<void> {
  return del(notificationsPath(tenantId, `/targets${id(targetId)}`));
}

/** The member's own preferences; email is off until they first save. */
export function getPreferences(tenantId: string | null, signal?: AbortSignal): Promise<Preferences> {
  return api(notificationsPath(tenantId, "/preferences"), { signal });
}

/** Turning email on needs the mail relay (503 `email_not_configured`); turning it off does not. */
export function savePreferences(tenantId: string | null, body: Preferences): Promise<Preferences> {
  return api(notificationsPath(tenantId, "/preferences"), { method: "PUT", body: JSON.stringify(body) });
}

// ---- The Inbox settings the Notifications page also edits (csa/console/inbox_settings.py) ------

/** The company's response targets and reminder timing; `version` is 0 until a first save. */
export function getInboxSettings(tenantId: string | null, signal?: AbortSignal): Promise<InboxSettings> {
  return api(settingsPath(tenantId ? `client_id=${encodeURIComponent(tenantId)}` : ""), { signal });
}

/** Admins only. Send the nine values and the version read (see `settingsBody`); 409 for a stale one. */
export function saveInboxSettings(tenantId: string | null, body: InboxSettings): Promise<InboxSettings> {
  return api(settingsPath(tenantId ? `client_id=${encodeURIComponent(tenantId)}` : ""), {
    method: "PUT",
    body: JSON.stringify(body),
  });
}
