import { api } from "./api";

/** Mirrors of csa/hr/personal.py and csa/hr/documents.py. */
export type EncryptedField = "ic_or_passport" | "bank_account" | "tax_number" | "epf_number" | "socso_number";
export type SelfField = "home_address" | "personal_phone" | "emergency_name" | "emergency_relationship" | "emergency_phone";
export type PlainField = SelfField | "date_of_birth" | "bank_name";
export type PersonalField = EncryptedField | PlainField;
export type DocumentKind = "contract" | "identity" | "certificate" | "other";

/**
 * A person's personal details as the page may read them. The five numbers arrive masked ("•••• 1234");
 * the full value only comes from `reveal`. `version` is null until the first save. `can_edit`: "all"
 * for the HR owner and Sensitive details holders, "contact" for the person themself (the five self fields).
 */
export interface Personal extends Record<PlainField | EncryptedField, string | null> {
  version: number | null;
  can_edit: "all" | "contact";
}
export interface PersonalReveal { value: string | null }
export interface PersonDocument {
  document_id: string;
  kind: DocumentKind;
  file_name: string;
  media_type: string;
  byte_size: number;
  uploaded_at: string;
  uploaded_by_name: string | null;
}
export interface DocumentList { items: PersonDocument[] }

/**
 * Reads and the two writes that are not HR operations. Saving personal details, marking someone as
 * leaving and rehiring go through the HR operations client. `key` is the caller's Idempotency-Key: the
 * form keeps one per attempt, so a retry after a lost response returns the first receipt.
 */
export function recordsClient(tenant: string) {
  const suffix = `client_id=${encodeURIComponent(tenant)}`;
  const person = (id: string, rest: string) => `/console/v1/hr/people/${encodeURIComponent(id)}${rest}?${suffix}`;
  const document = (id: string, rest: string) => `/console/v1/hr/documents/${encodeURIComponent(id)}${rest}?${suffix}`;
  return {
    personal: (id: string) => api<Personal>(person(id, "/personal")),
    /** One number in full. The server records every Show in the audit log. */
    reveal: (id: string, field: EncryptedField) =>
      api<PersonalReveal>(person(id, "/personal/reveal"), { method: "POST", body: JSON.stringify({ field }) }),
    documents: (id: string) => api<DocumentList>(person(id, "/documents")),
    /** One file per upload, sent as the multipart field `file`. */
    upload: (id: string, kind: DocumentKind, file: File, key: string) => {
      const form = new FormData();
      form.append("file", file);
      return api<PersonDocument>(`${person(id, "/documents")}&kind=${encodeURIComponent(kind)}`, {
        method: "POST", body: form, headers: { "Idempotency-Key": key },
      });
    },
    /** A same-origin link for an anchor: the browser sends the session cookie and the server sets a safe filename. */
    downloadHref: (documentId: string) => document(documentId, "/download"),
    removeDocument: (documentId: string, key: string) =>
      api<void>(document(documentId, "/remove"), { method: "POST", body: "{}", headers: { "Idempotency-Key": key } }),
  };
}
