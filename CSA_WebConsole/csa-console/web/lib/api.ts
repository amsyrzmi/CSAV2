export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    /** The server's raw `detail` code when it differs from the shown message. */
    public code: string = message,
    /** The decoded JSON error body, when there was one (for example `retry_after`). */
    public body?: Record<string, unknown>,
  ) {
    super(message);
  }
}

/** Server detail codes that deserve a sentence instead of the raw code. */
const explained: Record<string, string> = {
  package_inactive:
    "This area is not part of your organisation's active service packages. Ask your administrator to activate it in Services.",
  proposal_stale:
    "The record changed after this was proposed. Nothing was changed; review it and ask again.",
  proposal_consumed: "This proposal was already decided. Nothing more was changed.",
  proposal_expired: "This proposal expired. Nothing was changed; ask again if you still need it.",
  agent_disabled:
    "This assistant is switched off for your organisation. An administrator can turn it on in its settings.",
  planner_unavailable: "The assistant is unavailable right now. Try again in a moment.",
  mailbox_not_connected: "Connect your Gmail in the Email tab first.",
  whatsapp_number_in_use: "That WhatsApp number is already connected to another company.",
  whatsapp_token_rejected:
    "WhatsApp did not accept that access token for this phone number ID. Check both in your Meta app, then try again.",
  whatsapp_unreachable: "We could not reach WhatsApp to check the number. Wait a minute and try again.",
  // Invitations and the HR access chosen on them.
  hr_preset_requires_hr_owner: "Only the HR owner can give this access. Choose Staff or No HR access.",
  hr_not_available: "HR isn't switched on for this business.",
  invitation_already_handled: "This was already applied or dismissed. Reload to see the latest.",
  invitation_owner_required: "Only the HR owner can give this access. Ask them to apply or dismiss it.",
  invitation_rows_refused:
    "This member's access could not be changed here. Check their access in Advanced, then try again.",
  invitation_arguments_invalid: "We could not read that invitation. Reload to see the latest.",
  // Subscriptions (csa/subscriptions): plans, actions and the request queue.
  plan_unavailable: "That plan is not available for this company.",
  action_not_allowed: "This change is not possible from the subscription's current state.",
  request_decided: "Someone has already decided this request.",
  plan_unchanged:
    "The subscription is already on that plan. Choose a different plan or set a special price.",
  price_override_requires_immediate_change:
    "A special price can only start straight away. Clear the start date or the special price.",
  renews_at_before_start: "The renewal date must be after the start date. Choose a later renewal date.",
  renews_at_not_after_current: "Choose a renewal date after the current one.",
  resume_at_not_after_today: "Choose a resume date after today.",
  plan_in_use:
    "Add a new plan with the billing period you want. This plan's billing period cannot change while customers are on it.",
  proposal_registry_unavailable: "We could not prepare the change. Wait a minute and try again.",
  request_has_no_subscription:
    "This request is not linked to a subscription. Decline it, then make the change from the customer's subscription.",
  request_subscription_mismatch:
    "This request does not match the customer's subscription. Decline it, then check the subscription.",
  request_has_no_plan:
    "This request does not say which plan the customer wants. Decline it, then ask the customer.",
  request_action_mismatch:
    "This change is not the one the customer asked for. Approve the request from the Requests tab instead.",
  resume_at_applies_to_pause_only: "A resume date only applies to a pause. Clear the resume date and try again.",
  request_in_progress: "A colleague is deciding this request right now. Wait a moment, then refresh the page.",
  proposal_not_executed: "The change did not complete. Refresh the page to see where it stands.",
  plan_exists: "A plan with that code already exists. Choose a different code.",
  plan_not_found: "That plan no longer exists. Refresh the page.",
  subscription_not_found: "That subscription no longer exists. Refresh the page.",
  request_not_found: "That request no longer exists. Refresh the page.",
  contact_not_found: "That customer no longer exists. Refresh the page.",
};

async function decode<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    let code = message;
    let detail: Record<string, unknown> | undefined;
    try {
      const body: unknown = await response.json();
      if (
        typeof body === "object" &&
        body !== null &&
        "detail" in body &&
        typeof body.detail === "string"
      ) {
        code = body.detail;
        message = explained[code] ?? code;
        detail = body as Record<string, unknown>;
      }
    } catch {
      /* Non-JSON gateway failures keep their HTTP status. */
    }
    throw new ApiError(response.status, message, code, detail);
  }
  return response.status === 204
    ? (undefined as T)
    : (response.json() as Promise<T>);
}

export async function api<T = unknown>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  if (!path.startsWith("/console/v1/"))
    throw new Error("Only the CSAV2 console API is adopted");
  const headers = new Headers(init.headers);
  const method = (init.method ?? "GET").toUpperCase();
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
    const csrf = await decode<{ csrf_token: string }>(
      await fetch("/console/v1/auth/csrf", {
        credentials: "same-origin",
        cache: "no-store",
        signal: init.signal,
      }),
    );
    headers.set("X-CSRF-Token", csrf.csrf_token);
  }
  if (init.body instanceof FormData) {
    // The browser supplies the multipart boundary.
    headers.delete("Content-Type");
  } else if (init.body instanceof Blob) {
    headers.set("Content-Type", init.body.type || "application/octet-stream");
  } else if (init.body !== undefined) headers.set("Content-Type", "application/json");
  return decode<T>(
    await fetch(path, {
      ...init,
      headers,
      credentials: "same-origin",
      cache: "no-store",
    }),
  );
}

export const post = <T = void>(path: string, body: unknown = {}) =>
  api<T>(path, { method: "POST", body: JSON.stringify(body) });
export const patch = <T = void>(path: string, body: unknown) =>
  api<T>(path, { method: "PATCH", body: JSON.stringify(body) });
export const del = <T = void>(path: string) =>
  api<T>(path, { method: "DELETE" });
