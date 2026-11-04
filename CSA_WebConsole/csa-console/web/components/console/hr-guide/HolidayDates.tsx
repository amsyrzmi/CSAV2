"use client";

import { useId, useRef, useState } from "react";
import { addHolidayDate, HOLIDAY_LIST_MAX } from "@/lib/hr-guide";
import { field, secondary } from "../ui";

/**
 * The reviewed holiday dates: the same editable comma list as before (it is what gets saved), plus a
 * date picker and "Add date" that appends the chosen date to that list.
 */
export default function HolidayDates({ name, label }: { name: string; label: string }) {
  const [list, setList] = useState("");
  const [status, setStatus] = useState("");
  const date = useRef<HTMLInputElement>(null);
  const statusId = useId();
  function add() {
    const result = addHolidayDate(list, date.current?.value ?? "");
    if (result.list === undefined) { setStatus(result.error); return; }
    setList(result.list); setStatus("Date added to the list.");
    if (date.current) date.current.value = "";
  }
  return <div data-testid="hr-holiday-dates" className="space-y-2 sm:col-span-2">
    <label className="block text-sm">{label}
      <input className={`${field} mt-1`} name={name} type="text" value={list} maxLength={HOLIDAY_LIST_MAX}
        onChange={event => { setList(event.target.value); setStatus(""); }} /></label>
    <div className="flex flex-wrap items-end gap-2">
      <label className="block w-full min-w-0 text-sm sm:w-56">Pick a holiday date
        <input ref={date} className={`${field} mt-1`} type="date" data-testid="hr-holiday-date-input" aria-describedby={statusId}
          onKeyDown={event => { if (event.key === "Enter") { event.preventDefault(); add(); } }} /></label>
      <button type="button" className={secondary} data-testid="hr-holiday-add" onClick={add}>Add date</button>
    </div>
    <p id={statusId} role="status" className="min-h-5 text-sm text-[color:var(--fs-text-secondary)]">{status}</p>
  </div>;
}
