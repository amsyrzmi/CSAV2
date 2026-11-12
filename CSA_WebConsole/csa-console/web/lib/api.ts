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
  proposal_session_mismatch:
    "This proposal was made in a different sign-in session. Ask the assistant to prepare it again here.",
  agent_disabled:
    "This assistant is switched off for your organisation. An administrator can turn it on in its settings.",
  planner_unavailable: "The assistant is unavailable right now. Try again in a moment.",
  mailbox_not_connected: "Connect your Gmail in the Email tab first.",
  // Assistant and HR failures that have no card of their own.
  planner_invalid_output: "The assistant couldn't produce a usable answer. Try again or rephrase.",
  email_model_unavailable: "The email assistant couldn't write that right now. Try again in a moment.",
  inbound_email_blocked:
    "This email looks like it tries to give the assistant instructions, so no reply was drafted. Read it yourself and reply by hand.",
  hr_model_unavailable: "The HR assistant couldn't answer right now. Try again in a moment.",
  email_timeout_check_saved_state:
    "The email request took too long. Check your drafts and the Email assistant tab before trying again.",
  hr_agent_timeout_check_saved_state:
    "The HR request took too long. Check the HR tab for what was saved before trying again.",
  training_not_draft:
    "That training request is no longer a draft, so it can't be submitted again. Open it in HR to see where it stands.",
  stale_version: "This changed after you opened it. Reload to see the latest, then try again.",
  invalid_transition: "That change isn't possible from the current status. Reload to see the latest.",
  csrf_required: "Your session needs a refresh. Reload the page and try again.",
  no_employee_link:
    "Your login isn't linked to an employee record, so you can't submit training requests. Ask your HR owner to link it.",
  hr_training_not_permitted:
    "You don't have permission to submit training requests. Ask your HR owner to give you access.",
  // Email refusals the assistant dock shows as they are.
  reply_target_unavailable: "I can't tell who to reply to in that conversation.",
  email_recipient_invalid: "Check the email address.",
  recipients_invalid: "Check the email addresses.",
  draft_rejected: "I couldn't write that email. Try rephrasing.",
  thread_body_unavailable: "I couldn't read that conversation from Gmail. Sync and try again.",
  resource_unavailable: "That item isn't available any more.",
  // A held reply gone before its card was made (the card's code) or at Confirm (the inbox's).
  reply_approval_not_found: "That held reply is no longer waiting for a decision. Refresh the conversation.",
  "reply approval not found": "That held reply is no longer waiting for a decision. Refresh the conversation.",
  whatsapp_number_in_use: "That WhatsApp number is already connected to another company.",
  whatsapp_token_rejected:
    "WhatsApp did not accept that access token for this phone number ID. Check both in your Meta app, then try again.",
  whatsapp_unreachable: "We could not reach WhatsApp to check the number. Wait a minute and try again.",
  // Telegram bot setup by the platform team.
  telegram_token_malformed: "That does not look like a Telegram bot token. Copy it again from BotFather.",
  telegram_token_rejected:
    "Telegram did not accept that bot token. Check it in BotFather, or make a new one, then try again.",
  telegram_unreachable: "We could not reach Telegram. Wait a minute and try again.",
  hooks_origin_not_configured:
    "Telegram isn't available yet because this deployment has no public hooks address. Set it first, then connect the bot.",
  telegram_bot_in_use:
    "That bot is already connected to another company. Disconnect it there first, or make a new bot for this company.",
  telegram_bot_replace:
    "This company already has a different bot. Disconnect it first, then connect the new one.",
  telegram_webhook_in_use:
    "That bot is already wired to something else. Replace it only if you are sure nothing else needs it.",
  telegram_connect_in_progress:
    "Another setup for this company is still running. Wait a moment and reload.",
  telegram_webhook_failed:
    "The bot was saved but Telegram would not register the webhook. Connect again with the same token.",
  telegram_not_connected: "No bot is connected for this company yet. Connect the bot first.",
  telegram_chat_invalid:
    "That chat ID is not valid. It is a number, and a group's ID starts with a minus sign.",
  telegram_chat_unreachable:
    "The bot could not message that chat. Add the bot to the staff group, or check the ID.",
  telegram_chat_forbidden: "The bot was removed from that chat or blocked. Add it back and try again.",
  telegram_credential_unavailable: "The saved bot token could not be read. Connect the bot again.",
  platform_admin_only: "Only the platform team can change this. Ask them to set it up.",
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
  // Customer sign-ups (csa/subscriptions/signup.py).
  plan_not_open:
    "That plan is closed to new sign-ups or archived, so the request was not approved. Open the plan to sign-ups, or decline the request.",
  signup_not_pending: "Someone has already decided this sign-up. Refresh the page.",
  signup_has_plan:
    "This customer already has that plan, so the request was not approved. Decline it.",
  signup_changed: "This sign-up changed while you were deciding it. Refresh the page and try again.",
  signup_not_found: "That sign-up no longer exists. Refresh the page.",
  // Linking contacts (csa/contacts/api.py). A refused link is "link_refused:<reason>" and changed nothing.
  "link_refused:telegram_conflict":
    "These two contacts each have their own Telegram account, so they can't be linked.",
  "link_refused:same_channel_identity":
    "These two contacts already chat on the same channel with different accounts, so they can't be linked.",
  "link_refused:table_conflict":
    "These two contacts both have a record that would clash if they were linked, such as a waiting sign-up for the same plan. Decide that first and try again.",
  unlink_window_closed: "This link is older than 30 days and can no longer be undone here.",
  link_not_active: "This link has already been undone. Refresh the page.",
  later_link_active: "A newer link on one of these contacts has to be undone first.",
  survivor_merged:
    "One of these contacts was linked into another contact since, so this link can't be undone here. Refresh the page.",
  contact_merged:
    "One of these contacts was already merged into another contact, so they can't be linked. If this is a review, choose Not the same person to close it.",
  link_not_found: "That link no longer exists. Refresh the page.",
  review_not_found: "That review no longer exists. Refresh the page.",
  review_not_open: "Someone has already settled this review. Refresh the page.",
  same_contact: "Choose a different contact to link.",
  // Bookings setup (csa/bookings/setup.py). Booking and calendar refusals carry their own sentence.
  "bookings setup requires an administrator": "Only an Admin can change the Bookings setup.",
  "bookings tenant is not authorized": "You can't open Bookings for this company.",
  version_conflict: "Someone else changed this while you were editing. Reload to see the latest, then try again.",
  person_exists: "That person is already on the list.",
  person_not_found: "That person is no longer on the list. Reload the page.",
  member_not_found: "That team member is not available. Reload the page.",
  employee_not_found: "That HR record is not available. Reload the page.",
  resource_not_found: "That room or shared thing is no longer on the list. Reload the page.",
  type_not_found: "That booking type no longer exists. Reload the page.",
  type_invalid: "Check the booking type's details and try again.",
  type_needs_people: "Choose at least one person for this booking type.",
  type_needs_resources: "Choose at least one room or shared thing for this booking type.",
  type_needs_address: "Enter the address where people meet.",
  type_needs_link: "Enter the meeting link. It must start with https://.",
  hours_invalid:
    "Each period must open before it closes. Hours can't run past midnight, so the latest closing time is 23:59.",
  hours_overlap: "Two periods on the same day overlap. Change one of them.",
  subject_not_found: "That person or room is no longer on the list. Reload the page.",
  subject_invalid: "Choose a person or a room.",
  closed_day_not_found: "That closed day is no longer there. Reload the page.",
  block_times_invalid: "The end must be after the start.",
  block_not_found: "That unavailable time is no longer there. Reload the page.",
  slot_taken: "That time was just taken.",
  stale: "This booking was changed by someone else. Reload to see the latest, then try again.",
  needs_acknowledging: "This time needs a second look before it can be booked.",
  refused: "Not available that day.",
  own_bookings_only: "You can only change bookings you made or are part of.",
  wrong_status: "That isn't possible from the booking's current status. Reload to see the latest.",
  not_started: "A booking can only be marked as a no-show once it has started.",
  person_inactive: "That person isn't taking bookings any more. Choose someone else.",
  resource_inactive: "That room isn't available any more. Choose another.",
  type_inactive: "That booking type is switched off. Choose another.",
  times_invalid: "The booking must end after it starts. Check the length.",
  minutes_required: "Enter how long it lasts to look for free times.",
  people_or_resources_required: "Choose a person or a room to look for free times.",
  window_invalid: "The end of the period must be after its start.",
  no_room_available: "No room is free for this booking type. Ask an Admin to check its rooms.",
  "bookings changes require staff or an administrator": "Only staff and Admins can change bookings.",
  nobody_to_book: "Nobody is free to take this booking. Check the booking type's people or choose someone.",
  people_required: "Choose who this booking is with.",
  booking_not_found: "That booking no longer exists. Reload the page.",
  private_meetings_only: "Only a meeting can be private.",
  window_too_large: "That is too many days to show at once.",
  "customer details are not available to read-only members":
    "Customer details are not available to read-only members.",
  person_required:
    "Your account isn't on the Bookings people list, so there is nothing to connect. Ask an Admin to add you.",
  not_connected: "Google Calendar isn't connected.",
  calendar_unavailable: "Google Calendar isn't switched on for this company yet.",
  // Retrying a plan (csa/workspace/plan.py retry_plan); proposal_session_mismatch is above.
  plan_nothing_to_retry:
    "Nothing is left to retry. The other steps were done, or have changed since you reviewed them.",
  plan_not_executed: "This plan hasn't run yet, so there is nothing to retry.",
  proposal_not_retryable: "Only a plan from the assistant can be retried.",
  // Knowledge upload actions (csa/console/router.py); the Library reloads its lists after each refusal.
  original_removed: "The original was removed, so this can't be tried again. Remove it.",
  job_still_running: "This one is being read right now. The list has been refreshed.",
  not_retryable: "This one has already moved on. The list has been refreshed.",
  not_cancellable: "This one has already moved on. The list has been refreshed.",
  "KB capability is missing or disabled":
    "The knowledge assistant is switched off, so this can't be tried again right now.",
  "ingest job not found": "That upload is no longer on the list. The list has been refreshed.",
};

/** The English sentence for a server code, or undefined when none is written (own keys only). */
export function explainedMessage(code: string): string | undefined {
  return Object.hasOwn(explained, code) ? explained[code] : undefined;
}

/**
 * Booking refusals say their own sentence next to `detail` (the code), and the sentence is more
 * exact than anything written here, so it wins for these codes only. Every other page keeps
 * reading `detail` alone.
 */
const serverSentence = new Set(["stale", "refused", "needs_acknowledging"]);

async function decode<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let message =
      response.status >= 500
        ? "Something went wrong on our side. Try again."
        : `Request failed (${response.status})`;
    let code = `Request failed (${response.status})`;
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
        const said = "message" in body && typeof body.message === "string" ? body.message : "";
        message = (serverSentence.has(code) && said) || (explained[code] ?? code);
        detail = body as Record<string, unknown>;
      }
    } catch {
      /* Non-JSON gateway failures keep their HTTP status, or the plain server-error sentence. */
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
