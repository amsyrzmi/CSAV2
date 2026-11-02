import type { Language } from "./assistant-copy";
import { PEOPLE_COPY } from "./hr-people";
import { RECORDS_COPY } from "./hr-records";

/**
 * The HR refusals an assistant `hr_person` card can meet at Confirm (csa/workspace/hr_people.py
 * HrPersonExecutor), most of which only the database decides. English is the profile's own
 * sentence (lib/hr-records.ts and lib/hr-people.ts), so the dock and the profile say the same
 * thing; each code here has its Malay pair.
 */
export const HR_PERSON_REFUSALS_MS: Record<string, string> = {
  last_owner_leaving: "Pemilik HR tidak boleh ditandakan sebagai akan berhenti. Hubungi sokongan untuk memindahkan pemilikan HR kepada orang lain dahulu.",
  leaver_has_access: "Hanya pemilik HR boleh menandakan orang yang mempunyai akses HR sebagai akan berhenti.",
  last_day_past: "Pilih hari terakhir mulai hari ini.",
  leaving_final: "Orang ini sudah berhenti. Gunakan Rehire.",
  person_not_active: "Orang ini sudah berhenti.",
  employee_exists: "Orang ini sudah ditambah. Muat semula untuk melihatnya.",
  manager_unavailable: "Pengurus itu tidak lagi tersedia. Pilih pengurus lain.",
  manager_loop: "Pengurus itu sudah melapor kepada orang ini. Pilih pengurus lain.",
  manager_is_you: "Anda tidak boleh menjadikan diri anda pengurus seseorang. Minta pemilik HR.",
  own_manager_owner_only: "Hanya pemilik HR boleh menukar pengurus anda sendiri.",
  department_unavailable: "Jabatan itu tidak lagi tersedia. Pilih jabatan lain.",
  department_archived: "Jabatan itu telah diarkibkan. Pilih jabatan lain.",
  department_name_taken: "Jabatan dengan nama itu sudah wujud. Pilih nama lain.",
};

/** The sentence for a person card's Confirm refusal, or null for a code the card does not own. */
export function hrPersonRefusal(cause: unknown, language: Language): string | null {
  const code = typeof cause === "object" && cause !== null ? (cause as { code?: unknown }).code : undefined;
  if (typeof code !== "string" || !Object.hasOwn(HR_PERSON_REFUSALS_MS, code)) return null;
  if (language === "ms") return HR_PERSON_REFUSALS_MS[code];
  return Object.hasOwn(RECORDS_COPY.refusals, code) ? RECORDS_COPY.refusals[code] : PEOPLE_COPY.refusals[code];
}
