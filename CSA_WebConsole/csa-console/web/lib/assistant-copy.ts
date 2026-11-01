/**
 * The assistant panel's own text in English and Malay (no React, no I/O). A turn or card carries
 * `language`; every line the panel writes around it comes from here, in that language.
 *
 * The Malay is standard Malaysian Malay with "anda" and uses the server's words
 * (csa/workspace/executors.py `_CARD_TEXT["ms"]`, service.py `_TEXT["ms"]`): "e-mel", "Kepada",
 * "Hantar". On-screen names stay as the console shows them ("Gmail", tab names), so tab names
 * are passed in as `{tab}`, never translated here.
 */
import { emailErrorSentence } from "@/lib/email-recipients";

export type Language = "en" | "ms";

export function languageOf(x: { language?: Language } | null | undefined): Language {
  return x?.language === "ms" ? "ms" : "en";
}

const en = {
  // Dock chrome.
  dock_title: "Assistant",
  new_conversation: "New conversation",
  close_assistant: "Close assistant",
  message_label: "Message",
  empty_hint:
    "Ask about this tab, find a colleague, or prepare an action. Nothing changes until you confirm it.",
  retry_same: "Retry same request",
  start_new: "Start a new request",
  // Order card labels.
  order_source: "Source",
  order_tracking: "Tracking",
  order_carrier: "Carrier",
  order_not_recorded: "Not recorded",
  // Dock: context line, loading, notices.
  on_tab: "On {tab}",
  no_tab_context: "No tab context",
  loading: "Loading your conversation…",
  resume_opening: "Opening {tab} and continuing…",
  resume_timeout: "The tab did not open, so the request was not continued. Send it again.",
  choose_colleague: "Choose a colleague:",
  no_colleague: "No matching colleague was found.",
  navigate_opened: "Opened {tab}.",
  navigate_opened_resume: "Opened {tab} and continued there.",
  navigate_unavailable: "{tab} is not available to you.",
  // Buttons and their busy forms.
  button_confirm: "Confirm",
  button_confirming: "Confirming…",
  button_send: "Send",
  button_sending: "Sending…",
  button_cancel: "Cancel",
  button_cancelling: "Cancelling…",
  expires: "Expires {when}",
  // Card status lines.
  summary_cancelled: "Cancelled. Nothing was changed.",
  summary_expired: "This proposal expired. Nothing was changed.",
  summary_requested: "Waiting for your confirmation.",
  summary_confirmed: "Confirmed. The action is being completed.",
  summary_export: "Saved to your Gmail drafts. Nothing was sent.",
  summary_sent_at: "Sent at {when}.",
  summary_sent: "Sent from your Gmail.",
  summary_training_status: "Submitted “{title}” (now {status}).",
  summary_training_title: "Submitted “{title}”.",
  summary_training: "Training request submitted.",
  summary_approval_expired: "Approval expired.",
  summary_followup_queued: "Follow-up reply queued.",
  summary_no_reply_queued: "No reply queued.",
  summary_already_handled: "Already handled. Nothing was changed.",
  summary_decision: "Your decision on the held reply was recorded.",
  summary_reply_queued: "Reply queued for delivery.",
  summary_subscription_status: "Subscription updated. It is now {status}.",
  summary_subscription: "Subscription updated.",
  // Result notices and page guidance.
  denied_unavailable: "The requested record is unavailable in this tenant.",
  denied: "This request could not be authorized.",
  unavailable: "The assistant could not complete this request.",
  guidance_none: "No guidance is available for this page.",
  guidance_workspace_home_help:
    "Use this internal workspace for staff guidance and authorized local business reads. Customer sandbox conversations are separate.",
  guidance_business_orders_help:
    "Business order views read tenant-scoped local records only. They do not query a marketplace or change an order.",
  guidance_business_order_help:
    "This order lookup reads one tenant-scoped local record. Confirm its source and local update time before relying on it.",
  // Errors the dock or a card shows when nothing more specific applies.
  error_failed: "The assistant request failed.",
  error_server: "Something went wrong on our side. Try again.",
  // The email preview inside a card.
  email_to: "To",
  email_cc: "CC",
  email_bcc: "BCC",
  email_subject: "Subject",
  email_reply_note:
    "Replies go to the address the email asked for, which may not be the person who wrote it. Check the To address.",
  email_open_failed: "We couldn't open this email. Refresh the page and try again.",
} as const;

export type PanelKey = keyof typeof en;

const ms: Record<PanelKey, string> = {
  dock_title: "Pembantu",
  new_conversation: "Perbualan baharu",
  close_assistant: "Tutup pembantu",
  message_label: "Mesej",
  empty_hint:
    "Tanya tentang tab ini, cari rakan sekerja, atau sediakan satu tindakan. Tiada apa-apa yang berubah sehingga anda mengesahkannya.",
  retry_same: "Cuba semula permintaan yang sama",
  start_new: "Mulakan permintaan baharu",
  order_source: "Sumber",
  order_tracking: "Penjejakan",
  order_carrier: "Kurier",
  order_not_recorded: "Tidak direkodkan",
  on_tab: "Pada tab {tab}",
  no_tab_context: "Tiada konteks tab",
  loading: "Memuatkan perbualan anda…",
  resume_opening: "Membuka tab {tab} dan meneruskan…",
  resume_timeout: "Tab tidak dibuka, jadi permintaan tidak diteruskan. Hantar semula.",
  choose_colleague: "Pilih rakan sekerja:",
  no_colleague: "Tiada rakan sekerja yang sepadan ditemui.",
  navigate_opened: "Tab {tab} dibuka.",
  navigate_opened_resume: "Tab {tab} dibuka dan diteruskan di sana.",
  navigate_unavailable: "Tab {tab} tidak tersedia untuk anda.",
  button_confirm: "Sahkan",
  button_confirming: "Mengesahkan…",
  button_send: "Hantar",
  button_sending: "Menghantar…",
  button_cancel: "Batal",
  button_cancelling: "Membatalkan…",
  expires: "Tamat tempoh {when}",
  summary_cancelled: "Dibatalkan. Tiada apa-apa yang diubah.",
  summary_expired: "Cadangan ini telah tamat tempoh. Tiada apa-apa yang diubah.",
  summary_requested: "Menunggu pengesahan anda.",
  summary_confirmed: "Disahkan. Tindakan sedang diselesaikan.",
  summary_export: "Disimpan ke draf Gmail anda. Tiada apa-apa yang dihantar.",
  summary_sent_at: "Dihantar pada {when}.",
  summary_sent: "Dihantar dari Gmail anda.",
  summary_training_status: "Permohonan “{title}” dihantar (status kini: {status}).",
  summary_training_title: "Permohonan “{title}” dihantar.",
  summary_training: "Permohonan latihan dihantar.",
  summary_approval_expired: "Kelulusan telah tamat tempoh.",
  summary_followup_queued: "Balasan susulan dimasukkan dalam baris gilir.",
  summary_no_reply_queued: "Tiada balasan dimasukkan dalam baris gilir.",
  summary_already_handled: "Sudah dikendalikan. Tiada apa-apa yang diubah.",
  summary_decision: "Keputusan anda tentang balasan yang ditahan telah direkodkan.",
  summary_reply_queued: "Balasan dimasukkan dalam baris gilir untuk dihantar.",
  summary_subscription_status: "Langganan dikemas kini. Kini {status}.",
  summary_subscription: "Langganan dikemas kini.",
  denied_unavailable: "Rekod yang diminta tidak tersedia dalam ruang kerja ini.",
  denied: "Permintaan ini tidak dapat dibenarkan.",
  unavailable: "Pembantu tidak dapat menyelesaikan permintaan ini.",
  guidance_none: "Tiada panduan tersedia untuk halaman ini.",
  guidance_workspace_home_help:
    "Gunakan ruang kerja dalaman ini untuk panduan kakitangan dan bacaan data perniagaan tempatan yang dibenarkan. Perbualan sandbox pelanggan adalah berasingan.",
  guidance_business_orders_help:
    "Paparan pesanan perniagaan hanya membaca rekod tempatan bagi ruang kerja ini. Ia tidak membuat pertanyaan kepada pasar dalam talian dan tidak mengubah pesanan.",
  guidance_business_order_help:
    "Carian pesanan ini membaca satu rekod tempatan bagi ruang kerja ini. Sahkan sumber dan masa kemas kini tempatannya sebelum bergantung padanya.",
  error_failed: "Sesuatu tidak kena. Cuba lagi.",
  error_server: "Sesuatu tidak kena di pihak kami. Cuba lagi.",
  email_to: "Kepada",
  email_cc: "CC",
  email_bcc: "BCC",
  email_subject: "Subjek",
  email_reply_note:
    "Balasan dihantar ke alamat yang diminta oleh e-mel itu, yang mungkin bukan orang yang menulisnya. Semak alamat Kepada.",
  email_open_failed: "Kami tidak dapat membuka e-mel ini. Muat semula halaman dan cuba lagi.",
};

export const PANEL: Record<Language, Record<PanelKey, string>> = { en, ms };

/** The panel's line for `key` in `language`, with each `{name}` filled from `vars`. */
export function panelText(
  language: Language,
  key: PanelKey,
  vars: Record<string, string | number> = {},
): string {
  return PANEL[language][key].replace(/\{(\w+)\}/g, (whole, name: string) =>
    name in vars ? String(vars[name]) : whole,
  );
}

/** How a status the receipt reports is said in Malay; an unknown one is shown as it came. */
const MS_STATUS: Record<string, string> = {
  // Training requests (csa/hr TrainingStatus).
  draft: "draf",
  submitted: "menunggu kelulusan",
  approved: "diluluskan",
  rejected: "ditolak",
  withdrawn: "ditarik balik",
  // Subscriptions (csa/subscriptions Status).
  active: "aktif",
  paused: "dijeda",
  cancelled: "dibatalkan",
};

/** A receipt status as the card's language says it ("pending_approval" reads "pending approval"). */
export function statusText(status: string, language: Language): string {
  return language === "ms" ? (MS_STATUS[status] ?? status.replaceAll("_", " ")) : status.replaceAll("_", " ");
}

/**
 * `ApiError.code` to a Malay line, for the codes an assistant turn or a card Confirm/Cancel can
 * return (lib/api.ts has the English). Email refusals not listed here are in
 * lib/email-recipients.ts, which holds the Malay email sentences once (so a code listed there,
 * such as mailbox_not_connected, is not repeated here).
 */
export const MS_ERRORS: Record<string, string> = {
  package_inactive:
    "Kawasan ini bukan sebahagian daripada pakej perkhidmatan aktif organisasi anda. Minta pentadbir anda mengaktifkannya di halaman Services.",
  proposal_stale:
    "Rekod berubah selepas ini dicadangkan. Tiada apa-apa yang diubah; semak dan minta semula.",
  proposal_consumed: "Cadangan ini sudah diputuskan. Tiada apa-apa lagi yang diubah.",
  proposal_expired:
    "Cadangan ini telah tamat tempoh. Tiada apa-apa yang diubah; minta semula jika anda masih memerlukannya.",
  proposal_session_mismatch:
    "Cadangan ini dibuat dalam sesi log masuk yang lain. Minta pembantu menyediakannya semula di sini.",
  agent_disabled:
    "Pembantu ini dimatikan untuk organisasi anda. Pentadbir boleh menghidupkannya dalam tetapannya.",
  planner_unavailable: "Pembantu tidak tersedia sekarang. Cuba lagi sebentar lagi.",
  planner_invalid_output:
    "Pembantu tidak dapat menghasilkan jawapan yang boleh digunakan. Cuba lagi atau nyatakan dengan cara lain.",
  hr_model_unavailable: "Pembantu HR tidak dapat menjawab sekarang. Cuba lagi sebentar lagi.",
  email_timeout_check_saved_state:
    "Permintaan e-mel mengambil masa terlalu lama. Semak draf anda dan tab Email assistant sebelum mencuba lagi.",
  hr_agent_timeout_check_saved_state:
    "Permintaan HR mengambil masa terlalu lama. Semak tab People & HR untuk apa yang telah disimpan sebelum mencuba lagi.",
  training_not_draft:
    "Permohonan latihan itu bukan lagi draf, jadi ia tidak boleh dihantar semula. Bukanya dalam HR untuk melihat statusnya.",
  stale_version:
    "Ini telah berubah selepas anda membukanya. Muat semula untuk melihat yang terkini, kemudian cuba lagi.",
  invalid_transition:
    "Perubahan itu tidak boleh dilakukan daripada status semasa. Muat semula untuk melihat yang terkini.",
  csrf_required: "Sesi anda perlu disegarkan. Muat semula halaman dan cuba lagi.",
  no_employee_link:
    "Log masuk anda tidak dipautkan kepada rekod pekerja, jadi anda tidak boleh menghantar permohonan latihan. Minta pemilik HR anda memautkannya.",
  hr_training_not_permitted:
    "Anda tiada kebenaran menghantar permohonan latihan. Minta pemilik HR anda memberi anda akses.",
  reply_target_unavailable: "Saya tidak dapat menentukan kepada siapa untuk membalas dalam perbualan itu.",
  email_recipient_invalid: "Semak alamat e-mel.",
  draft_rejected: "Saya tidak dapat menulis e-mel itu. Cuba nyatakan dengan cara lain.",
  thread_body_unavailable: "Saya tidak dapat membaca perbualan itu daripada Gmail. Segerakkan dan cuba lagi.",
  resource_unavailable: "Item itu tidak tersedia lagi.",
  reply_approval_not_found:
    "Balasan yang ditahan itu tidak lagi menunggu keputusan. Segarkan perbualan.",
  // Subscription card refusals at Confirm (csa/subscriptions/executor.py); retrying cannot help.
  action_not_allowed: "Perubahan ini tidak boleh dilakukan daripada keadaan langganan semasa.",
  resume_at_not_after_today: "Pilih tarikh sambung semula selepas hari ini.",
  plan_unavailable: "Pelan itu tidak tersedia untuk syarikat ini.",
  plan_unchanged:
    "Langganan sudah berada pada pelan itu. Pilih pelan yang berbeza atau tetapkan harga istimewa.",
  price_override_requires_immediate_change:
    "Harga istimewa hanya boleh bermula serta-merta. Kosongkan tarikh mula atau harga istimewa.",
  renews_at_not_after_current: "Pilih tarikh pembaharuan selepas tarikh yang semasa.",
  request_decided: "Seseorang sudah membuat keputusan tentang permintaan ini.",
  request_subscription_mismatch:
    "Permintaan ini tidak sepadan dengan langganan pelanggan. Tolak permintaan itu, kemudian semak langganan.",
  request_action_mismatch:
    "Perubahan ini bukan yang diminta oleh pelanggan. Luluskan permintaan itu daripada tab Requests.",
  subscription_not_found: "Langganan itu tidak wujud lagi. Muat semula halaman.",
  // The inbox's own spelling of the same refusal, which api.ts also explains.
  "reply approval not found":
    "Balasan yang ditahan itu tidak lagi menunggu keputusan. Segarkan perbualan.",
};

/**
 * The line for a failed turn or card action. English is what `api.ts` already explained in
 * `message` (an email refusal it left as the bare code gets the email sentence). Malay goes by
 * `ApiError.code`, never the English `message`: a mapped code, else an email sentence, else the
 * server-error line for a 5xx, else one fallback.
 */
export function errorLine(cause: unknown, language: Language): string {
  const code = typeof cause === "object" && cause !== null ? (cause as { code?: unknown }).code : undefined;
  if (language === "ms") {
    if (typeof code === "string") {
      const mapped = MS_ERRORS[code] ?? emailErrorSentence(cause, "ms");
      if (mapped) return mapped;
    }
    const status = (cause as { status?: unknown } | null)?.status;
    return typeof status === "number" && status >= 500
      ? panelText("ms", "error_server")
      : panelText("ms", "error_failed");
  }
  if (!(cause instanceof Error)) return panelText("en", "error_failed");
  if (cause.message === code) return emailErrorSentence(cause) ?? cause.message;
  return cause.message;
}
