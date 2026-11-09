import { api, del } from "./api";

export interface WhatsAppNumber {
  connected: boolean;
  display_phone: string | null;
  phone_number_id_last4: string | null;
  waba_id: string | null;
  enabled: boolean;
  callback_url: string | null;
  connected_at: string | null;
}
export interface WhatsAppNumberInput {
  phone_number_id: string;
  waba_id: string;
  access_token: string;
}

/** `tenantId` is null for a tenant member and the target tenant for a platform admin. */
function path(tenantId: string | null) {
  const suffix = tenantId ? `?client_id=${encodeURIComponent(tenantId)}` : "";
  return `/console/v1/channels/whatsapp${suffix}`;
}

export const getWhatsAppNumber = (tenantId: string | null) =>
  api<WhatsAppNumber>(path(tenantId));
/**
 * The access token travels only in this request body; the server never returns it.
 * The server checks the number with WhatsApp first and takes the display number from it.
 */
export const connectWhatsAppNumber = (tenantId: string | null, body: WhatsAppNumberInput) =>
  api<WhatsAppNumber>(path(tenantId), { method: "PUT", body: JSON.stringify(body) });
export const disconnectWhatsAppNumber = (tenantId: string | null) =>
  del(path(tenantId));

export interface TelegramStaffChat {
  address: string;
  verified: boolean;
}
export interface TelegramStatus {
  connected: boolean;
  bot_id: string | null;
  bot_username: string | null;
  webhook_registered: boolean;
  connected_at: string | null;
  staff_chat: TelegramStaffChat | null;
  hooks_ready: boolean;
}
export interface TelegramCheck {
  webhook_url_matches: boolean;
  pending_update_count: number;
  last_error: string | null;
}
export interface TelegramConnectInput {
  bot_token: string;
  staff_chat_id?: string;
  replace_webhook?: boolean;
}

function telegramPath(tenantId: string | null, suffix = "") {
  const query = tenantId ? `?client_id=${encodeURIComponent(tenantId)}` : "";
  return `/console/v1/channels/telegram${suffix}${query}`;
}

export const getTelegram = (tenantId: string | null) =>
  api<TelegramStatus>(telegramPath(tenantId));
/**
 * The bot token travels only in this request body; the server never returns it. Telegram
 * accepts the token before anything is stored. Only a platform admin can call this.
 */
export const connectTelegram = (tenantId: string | null, input: TelegramConnectInput) => {
  const body: TelegramConnectInput = { bot_token: input.bot_token };
  if (input.staff_chat_id) body.staff_chat_id = input.staff_chat_id;
  if (input.replace_webhook) body.replace_webhook = true;
  return api<TelegramStatus>(telegramPath(tenantId), { method: "PUT", body: JSON.stringify(body) });
};
/** The bot sends a test message to the chat first; only a delivered message saves it. */
export const setTelegramStaffChat = (tenantId: string | null, chatId: string) =>
  api<TelegramStatus>(telegramPath(tenantId, "/staff-chat"), {
    method: "PUT", body: JSON.stringify({ chat_id: chatId }),
  });
export const checkTelegram = (tenantId: string | null) =>
  api<TelegramCheck>(telegramPath(tenantId, "/check"), { method: "POST" });
export const disconnectTelegram = (tenantId: string | null) =>
  del(telegramPath(tenantId));

/** Delivery fields the inbox carries for each message. */
export interface DeliveryState {
  delivery_status: string | null;
  delivery_error: string | null;
  provider_status: "sent" | "delivered" | "read" | "failed" | null;
  provider_error: string | null;
}

const SERVICE_WINDOW =
  "WhatsApp only allows replies within 24 hours of the customer's last message. Wait for the customer to message again.";
/** Meta's re-engagement code: the 24-hour window had closed. */
const META_WINDOW_CLOSED = "131047";
const DEAD_NOTICES: Record<string, string> = {
  outside_service_window: SERVICE_WINDOW,
  recipient_not_on_whatsapp: "Not delivered. This customer's number does not use WhatsApp.",
  token_invalid:
    "Not delivered. The WhatsApp connection stopped working. Ask your administrator to connect the number again in Services.",
  whatsapp_endpoint_unavailable:
    "Not delivered. This company's WhatsApp number is disconnected. Ask your administrator to connect it again in Services.",
  channel_not_configured: "Not delivered. WhatsApp is not switched on yet. Ask your administrator.",
};

/** The short status after the author, or null when the message has no delivery. */
export function deliveryLabel(message: DeliveryState): string | null {
  if (message.provider_status === "failed") return "not delivered";
  if (!message.delivery_status) return null;
  return message.delivery_status === "pending" ? "queued" : message.delivery_status;
}

/** A plain-words line for a reply the customer did not get, or null. */
export function deliveryNotice(message: DeliveryState): string | null {
  const dead = message.delivery_error ? DEAD_NOTICES[message.delivery_error] : undefined;
  if (dead) return dead;
  if (message.provider_status !== "failed") return null;
  return message.provider_error === META_WINDOW_CLOSED
    ? SERVICE_WINDOW
    : "Not delivered. WhatsApp could not pass this reply to the customer.";
}
