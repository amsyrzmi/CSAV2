import { dateText, LEAVING_REASONS, PERSONAL_LABELS, peopleRefusalText } from "./hr-people";
import type { LeavingReason, OrgChart, Person } from "./hr-people-api";
import type { Option } from "./hr-pickers";
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
  /** When a write may or may not have landed. The form keeps its Idempotency-Key, so choosing the button again is safe. */
  uncertain: {
    save: "We couldn't confirm the save. Choose Save changes again; it won't save twice.",
    upload: "We couldn't confirm the upload. Choose Upload again; it won't upload twice.",
    remove: "We couldn't confirm the removal. Choose Remove again; it won't remove twice.",
  },
  /** Plain sentences for the codes the personal, document, leaving and rehire operations answer with. */
  refusals: {
    last_owner_leaving: "The HR owner can't be marked as leaving. Ask your admin to move HR ownership to someone else first.",
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

/**
 * The sentence for a failed personal save, upload or removal. A 4xx is a definite answer, so its code picks
 * the sentence. A network error (`failure` null) or a 5xx leaves the outcome unknown.
 */
export function recordsErrorText(failure: { status: number; code: string } | null, action: "save" | "upload" | "remove"): string {
  if (failure && failure.status >= 400 && failure.status < 500) return recordsRefusalText(failure.code);
  return RECORDS_COPY.uncertain[action];
}

const LEAVING_UNCERTAIN = {
  leave: "We couldn't confirm the leaving date was saved. Choose Mark as leaving again; it won't be saved twice.",
  undo: "We couldn't confirm the change. Choose Undo leaving again; it won't happen twice.",
  rehire: "We couldn't confirm the rehire. Choose Rehire again; it won't happen twice.",
};

/** The sentence for a failed mark as leaving, undo or rehire: a 4xx is a definite answer, anything else may have landed. */
export function leavingErrorText(failure: { status: number; code: string } | null, action: keyof typeof LEAVING_UNCERTAIN): string {
  if (failure && failure.status >= 400 && failure.status < 500) return recordsRefusalText(failure.code);
  return LEAVING_UNCERTAIN[action];
}

/** The message after a personal save. A receipt version 0 means nothing was saved (no record exists). */
export function personalSavedText(receiptVersion: number): string {
  return receiptVersion === 0 ? "Nothing was saved because no details were entered." : "Personal details saved.";
}

/** "200 KB", "2.5 MB": a document's size in the list. */
export function fileSizeText(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.floor(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
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

type ProfileActions = { person: Pick<Person, "status">; can_edit: boolean };

/** Mark as leaving: an editor, on someone who is active or starting (not already leaving or left). */
export function canMarkLeaving(profile: ProfileActions, readonly: boolean): boolean {
  return profile.can_edit && !readonly && (profile.person.status === "active" || profile.person.status === "starting");
}
/** Undo leaving: an editor, while the person is Leaving (the server calls them that until the last day has passed). */
export function canUndoLeaving(profile: ProfileActions, readonly: boolean): boolean {
  return profile.can_edit && !readonly && profile.person.status === "leaving";
}
/** Rehire: an editor, on someone who has left. */
export function canRehire(profile: ProfileActions, readonly: boolean): boolean {
  return profile.can_edit && !readonly && profile.person.status === "left";
}

/**
 * The "Their reports move to" picker. `options` are the people the viewer can pick, without the leaver
 * and without anyone below them (the server refuses those as a reporting loop). `defaultId` is the
 * leaver's own manager when that person is offered, else "" (no manager, top of the company).
 * `hasReports` is false only when the org chart is known and nobody reports to the leaver; with no
 * chart (it failed to load) the picker still shows, and the server decides.
 */
export function reportsMoveChoices(input: {
  options: readonly Option[]; chart: OrgChart | null; person: Pick<Person, "employee_id" | "manager_id">;
}): { options: Option[]; defaultId: string; hasReports: boolean } {
  const { chart, person } = input;
  const below = new Set<string>([person.employee_id]);
  if (chart) {
    const queue = [person.employee_id];
    for (let id = queue.shift(); id !== undefined; id = queue.shift())
      for (const node of chart.nodes)
        if (node.manager_id === id && !below.has(node.employee_id)) { below.add(node.employee_id); queue.push(node.employee_id); }
  }
  const options = input.options.filter(([id]) => !below.has(id));
  const defaultId = person.manager_id !== null && options.some(([id]) => id === person.manager_id) ? person.manager_id : "";
  const hasReports = chart === null || chart.nodes.some((node) => node.manager_id === person.employee_id);
  return { options, defaultId, hasReports };
}

/** The sentence that stops the leaving form before it is sent, else null. `today` is `YYYY-MM-DD` in Kuala Lumpur. */
export function leaveFormIssue(input: { lastDay: string; reason: LeavingReason; note: string }, today: string): string | null {
  const lastDay = input.lastDay.trim();
  if (!lastDay) return "Choose the last day.";
  if (lastDay < today) return RECORDS_COPY.refusals.last_day_past;
  if (input.reason === "other") {
    const note = input.note.trim();
    if (!note) return "Say what the reason is, in a few words.";
    if (note.length > MAX_NOTE_LENGTH) return `Keep the reason to ${MAX_NOTE_LENGTH} characters or fewer.`;
  }
  return null;
}

export function rehireFormIssue(startDate: string): string | null {
  return startDate.trim() ? null : "Choose a start date.";
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
