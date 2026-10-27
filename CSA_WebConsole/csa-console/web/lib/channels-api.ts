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
  display_phone: string;
  access_token: string;
}

/** `tenantId` is null for a tenant member and the target tenant for a platform admin. */
function path(tenantId: string | null) {
  const suffix = tenantId ? `?client_id=${encodeURIComponent(tenantId)}` : "";
  return `/console/v1/channels/whatsapp${suffix}`;
}

export const getWhatsAppNumber = (tenantId: string | null) =>
  api<WhatsAppNumber>(path(tenantId));
/** The access token travels only in this request body; the server never returns it. */
export const connectWhatsAppNumber = (tenantId: string | null, body: WhatsAppNumberInput) =>
  api<WhatsAppNumber>(path(tenantId), { method: "PUT", body: JSON.stringify(body) });
export const disconnectWhatsAppNumber = (tenantId: string | null) =>
  del(path(tenantId));
