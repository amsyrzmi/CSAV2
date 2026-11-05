"use client";

import { LATEST_CLOSE, WEEKDAYS, type HoursDays } from "@/lib/bookings-view";
import { secondary } from "../ui";
import { input, mutedText } from "./parts";

const timeInput = `${input} w-32 max-w-full`;

/**
 * Seven days of opening periods, Monday first. A closed day has none; a day may have several
 * (a break in the middle). Times are the company's clock, and nothing may close later than 23:59.
 */
export default function HoursEditor({
  days,
  onChange,
  zone,
}: {
  days: HoursDays;
  onChange: (days: HoursDays) => void;
  zone: string | null;
}) {
  const setDay = (weekday: number, rows: HoursDays[number]) =>
    onChange(days.map((old, index) => (index === weekday ? rows : old)));
  return (
    <div data-testid="hours-editor" className="space-y-2">
      <p className={mutedText}>
        {zone ? `Times are in your company's time zone, ${zone}. ` : ""}
        Hours can&apos;t run past midnight, so the latest closing time is {LATEST_CLOSE}.
      </p>
      <ul className="space-y-2">
        {WEEKDAYS.map((name, weekday) => {
          const rows = days[weekday];
          return (
            <li key={name} data-testid={`hours-day-${weekday}`}
              className="rounded-[var(--fs-radius-control)] border border-[color:var(--fs-border)] p-2">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="text-sm font-medium">{name}</span>
                {rows.length === 0 ? (
                  <button type="button" className={secondary}
                    onClick={() => setDay(weekday, [{ opens: "09:00", closes: "18:00" }])}>
                    Open this day
                    <span className="sr-only">: {name}</span>
                  </button>
                ) : (
                  <button type="button" className={secondary} onClick={() => setDay(weekday, [])}>
                    Close this day
                    <span className="sr-only">: {name}</span>
                  </button>
                )}
              </div>
              {rows.length === 0 ? (
                <p className={`mt-1 ${mutedText}`}>Closed</p>
              ) : (
                <div className="mt-2 space-y-2">
                  {rows.map((row, index) => (
                    <div key={index} className="flex flex-wrap items-center gap-2">
                      <input type="time" aria-label={`${name} opens`} className={timeInput}
                        min="00:00" max={LATEST_CLOSE} value={row.opens}
                        onChange={(event) =>
                          setDay(weekday, rows.map((old, at) => (at === index ? { ...old, opens: event.target.value } : old)))} />
                      <span className="text-sm">to</span>
                      <input type="time" aria-label={`${name} closes`} className={timeInput}
                        min="00:00" max={LATEST_CLOSE} value={row.closes}
                        onChange={(event) =>
                          setDay(weekday, rows.map((old, at) => (at === index ? { ...old, closes: event.target.value } : old)))} />
                      {rows.length > 1 && (
                        <button type="button" className={secondary} aria-label={`Remove this period on ${name}`}
                          onClick={() => setDay(weekday, rows.filter((_, at) => at !== index))}>
                          Remove
                        </button>
                      )}
                    </div>
                  ))}
                  <button type="button" className={secondary}
                    onClick={() => setDay(weekday, [...rows, { opens: rows[rows.length - 1].closes, closes: "" }])}>
                    Add another period
                    <span className="sr-only"> on {name}</span>
                  </button>
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
