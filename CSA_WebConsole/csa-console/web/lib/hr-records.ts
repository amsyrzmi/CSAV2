import { dateText, LEAVING_REASONS, PERSONAL_LABELS, peopleRefusalText } from "./hr-people";
import type { LeavingReason, Person } from "./hr-people-api";
import type { DocumentKind, EncryptedField, Personal, PersonalField, PlainField, SelfField } from "./hr-records-api";

/** Pure helpers for personal details, documents and leaving: form bodies, file checks, plain words. */

export const MAX_DOCUMENT_BYTES = 10 * 1024 * 1024;
const MAX_NAME_LENGTH = 255;
const MAX_NOTE_LENGTH = 200;
const DOCUMENT_EXTENSION = /\.(pdf|jpe?g|png|docx)$/i;

export const RECORDS_COPY = {
  reasons: LEAVING_REASONS,
  kinds: [
    ["contract", "Contract"], ["identity", "Identity"], ["certificate", "Certificate"], ["other", "Other"],
  ] as [DocumentKind, string][],
  file: {
    type: "Choose a PDF, JPG, PNG or Word (.docx) file.",
    size: "Files can be up to 10 MB.",
    empty: "That file is empty.",
    name: "Rename the file and try again.",
  },
  /** Plain sentences for the codes the personal, document, leaving and rehire operations answer with. */
  refusals: {
    last_owner_leaving: "Make someone else the HR owner first. The company always needs one.",
    leaver_has_access: "Only the HR owner can mark someone with HR access as leaving.",
    last_day_past: "Choose a last day from today onwards.",
    leaving_final: "This person has already left. Use Rehire instead.",
    not_left: "Only someone who has left can be rehired.",
    person_not_active: "This person has already left.",
    personal_field_refused: "You can only change your own address, personal phone and emergency contact.",
    hr_document_type_unsupported: "Choose a PDF, JPG, PNG or Word (.docx) file.",
    hr_document_empty: "That file is empty.",
    hr_document_name_invalid: "Rename the file and try again.",
    hr_documents_unavailable: "Documents aren't set up for this company yet. Ask your administrator.",
    hr_document_file_unavailable: "This file can't be opened right now. Try again later or ask your administrator.",
    // The upload route's own messages for a file over the limit or a form that is not one file.
    "file exceeds 10 MB": "Files can be up to 10 MB.",
    "upload one file": "Choose one file at a time.",
    idempotency_conflict: "A different request already used this attempt. Reload the page and try again.",
    unsupported_operation: "This change isn't available. Reload the page and try again.",
    hr_retry_required: "Another change was in progress. Try again.",
    // A 422 never names a field or echoes a value, so the sentence covers every rule the save checks.
    invalid_hr_input: "Check the details you entered. Phone numbers take digits, spaces and + ( ) -. "
      + "Numbers take letters, digits and dashes, and the date of birth can't be in the future.",
  } as Record<string, string>,
};

/** The sentence for a refusal code from a records operation. People codes (stale version, manager) share their sentences. */
export function recordsRefusalText(code: string): string {
  return Object.hasOwn(RECORDS_COPY.refusals, code) ? RECORDS_COPY.refusals[code] : peopleRefusalText(code);
}

export interface PersonalFieldSpec {
  id: PersonalField;
  label: string;
  /** Stored encrypted: shown masked and only sent when typed. */
  encrypted: boolean;
  /** The person edits it themself; every other field is for the HR owner and Sensitive details holders. */
  self: boolean;
}
const ENCRYPTED: ReadonlySet<string> = new Set<EncryptedField>(
  ["ic_or_passport", "bank_account", "tax_number", "epf_number", "socso_number"]);
const SELF: ReadonlySet<string> = new Set<SelfField>(
  ["home_address", "personal_phone", "emergency_name", "emergency_relationship", "emergency_phone"]);

/** Every personal field in page order. */
export const PERSONAL_FIELDS: PersonalFieldSpec[] = (Object.keys(PERSONAL_LABELS) as PersonalField[]).map((id) => ({
  id, label: PERSONAL_LABELS[id], encrypted: ENCRYPTED.has(id), self: SELF.has(id),
}));

/**
 * The form's raw values, all strings. The five number boxes start empty (placeholder "Leave blank to
 * keep"): the page never holds a stored number, only its mask, which is shown beside the box.
 */
export type PersonalFormValues = Record<PersonalField, string>;

export function personalFormValues(personal: Personal | null): PersonalFormValues {
  return Object.fromEntries(
    PERSONAL_FIELDS.map(({ id, encrypted }) => [id, encrypted ? "" : personal?.[id] ?? ""]),
  ) as PersonalFormValues;
}

export interface PersonalSaveBody {
  operation: "personal.save";
  employee_id: string;
  /** Null for the first save of a person. */
  expected_version: number | null;
  /** A plain field present here is replaced: blank clears it. A field left out is kept. */
  values: Partial<Record<PlainField, string | null>>;
  /** Only a number that was typed (its new value) or cleared (null); the rest are kept. */
  encrypted: Partial<Record<EncryptedField, string | null>>;
}

const blankToNull = (value: string) => value.trim() || null;
const MASK_BULLET = "•";

/**
 * The `personal.save` body. `mode` is the server's `can_edit`: "contact" is the person editing their own
 * record, which sends only the five self fields and no number. Every plain field the form shows is sent,
 * so a retry is the same save. A number is sent only on purpose: `cleared` (the "Clear" checkboxes) sends
 * null and wins over typed text; typed text is sent trimmed; a blank box keeps the stored number. Text
 * with a mask bullet counts as blank, so a pasted mask can never be saved as a number.
 */
export function personalBody(
  form: PersonalFormValues, original: Personal | null, mode: "all" | "contact", employeeId: string,
  cleared: ReadonlySet<EncryptedField> = new Set(),
): PersonalSaveBody {
  const values: PersonalSaveBody["values"] = {};
  const encrypted: PersonalSaveBody["encrypted"] = {};
  for (const field of PERSONAL_FIELDS) {
    if (mode === "contact" && !field.self) continue;
    const text = form[field.id];
    if (field.encrypted) {
      const id = field.id as EncryptedField;
      const typed = text.includes(MASK_BULLET) ? null : blankToNull(text);
      if (cleared.has(id)) encrypted[id] = null;
      else if (typed !== null) encrypted[id] = typed;
    } else if (field.id === "home_address") {
      // Line breaks belong to an address; the server stores them as \n.
      values[field.id] = blankToNull(text.replaceAll("\r\n", "\n"));
    } else values[field.id as PlainField] = blankToNull(text);
  }
  return {
    operation: "personal.save", employee_id: employeeId, expected_version: original?.version ?? null, values, encrypted,
  };
}

function needVersion(version: number | undefined): number {
  if (version === undefined) throw new Error("This change needs the record version");
  return version;
}

export interface LeaveBody {
  operation: "person.leave";
  employee_id: string;
  expected_version: number;
  last_day: string;
  reason: LeavingReason;
  note?: string;
  reports_new_manager: string | null;
}

/** The `person.leave` body. The note is required for Other and sent for no other reason. */
export function leaveBody(input: {
  employeeId: string; version?: number; lastDay: string; reason: LeavingReason; note: string; reportsNewManager: string | null;
}): LeaveBody {
  const version = needVersion(input.version);
  if (!input.lastDay.trim()) throw new Error("Choose the last day");
  const note = input.note.trim();
  if (input.reason === "other" && !note) throw new Error("A note is required when the reason is Other");
  if (note.length > MAX_NOTE_LENGTH) throw new Error(`The note can be at most ${MAX_NOTE_LENGTH} characters`);
  return {
    operation: "person.leave", employee_id: input.employeeId, expected_version: version, last_day: input.lastDay.trim(),
    reason: input.reason, ...(input.reason === "other" ? { note } : {}),
    reports_new_manager: input.reportsNewManager?.trim() || null,
  };
}

export function leaveUndoBody(input: { employeeId: string; version?: number }) {
  return { operation: "person.leave_undo" as const, employee_id: input.employeeId, expected_version: needVersion(input.version) };
}

export function rehireBody(input: { employeeId: string; version?: number; startDate: string }) {
  const version = needVersion(input.version);
  if (!input.startDate.trim()) throw new Error("Choose a start date");
  return { operation: "person.rehire" as const, employee_id: input.employeeId, expected_version: version, start_date: input.startDate.trim() };
}

/** "Leaving on 30 Nov 2026 (Resigned)", or null when no last day is set. The reason is HR-only, so it may be missing. */
export function leavingSummary(person: Pick<Person, "status" | "last_day"> & { leaving?: Person["leaving"] }): string | null {
  if (!person.last_day) return null;
  const reason = person.leaving
    ? RECORDS_COPY.reasons.find(([id]) => id === person.leaving?.reason)?.[1] ?? person.leaving.reason
    : null;
  const detail = reason && person.leaving?.reason === "other" && person.leaving.note ? `${reason}: ${person.leaving.note}` : reason;
  return `${person.status === "left" ? "Left" : "Leaving"} on ${dateText(person.last_day)}${detail ? ` (${detail})` : ""}`;
}

/**
 * The sentence that stops an upload before it is sent, else null. The server checks the content too
 * (a file that lies about its type is refused there); this saves the round trip for the plain cases.
 */
export function documentIssue(file: { name: string; size: number }): string | null {
  const name = file.name;
  if (!name.trim() || name.length > MAX_NAME_LENGTH || /[\u0000-\u001f\u007f/\\]/.test(name)) return RECORDS_COPY.file.name;
  if (file.size === 0) return RECORDS_COPY.file.empty;
  if (!DOCUMENT_EXTENSION.test(name)) return RECORDS_COPY.file.type;
  if (file.size > MAX_DOCUMENT_BYTES) return RECORDS_COPY.file.size;
  return null;
}
