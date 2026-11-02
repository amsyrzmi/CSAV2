"use client";

import {
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type FocusEvent,
  type KeyboardEvent,
} from "react";
import { createPortal } from "react-dom";
import {
  WEEKDAY_NAMES,
  addDays,
  addMonths,
  compareDays,
  dayLabel,
  describeRange,
  monthGrid,
  monthLabel,
  parseDay,
  presets,
  selectDay,
  todayLocal,
  weekdayIndex,
  type DayRange,
} from "@/lib/date-range";

const smallButton =
  "inline-flex min-h-10 items-center justify-center gap-1.5 rounded-[var(--fs-radius-control)] border " +
  "border-[color:var(--fs-border-control)] bg-[var(--fs-surface)] px-3 text-[color:var(--fs-text)] " +
  "hover:border-[color:var(--fs-primary)] pointer-coarse:min-h-11";
const sizeSm = "text-[length:var(--fs-text-sm)] font-medium";
/** Space kept between the popover and the viewport edge, and between it and the button. */
const EDGE = 8;
/** Room under the button that is enough to open the calendar below it and scroll inside. */
const ROOM_BELOW = 320;
const FOCUSABLE =
  'a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])';

function Chevron({ direction }: { direction: "left" | "right" | "down" }) {
  const path = direction === "left" ? "m15 6-6 6 6 6" : direction === "right" ? "m9 6 6 6-6 6" : "m6 9 6 6 6-6";
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 24 24"
      width="16"
      height="16"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      className="shrink-0"
    >
      <path d={path} />
    </svg>
  );
}

/**
 * A button that shows the chosen days in words and opens a month calendar (a bottom sheet below
 * 640 px). Days are local calendar dates `YYYY-MM-DD`, both ends inclusive; turn them into
 * instants for the server with `toInstants`. Two clicks choose a range (the first sets one day,
 * the second the other end); a third starts again. The chosen days carry `aria-selected`, a
 * border and the words on the button, so colour is never the only sign; today is underlined.
 * Keys in the grid: arrows move a day or a week, Home and End the week's ends, PageUp and
 * PageDown the month, Enter or Space selects. Escape closes and returns focus to the button.
 * The parent owns `value`. `today` is for tests; it defaults to the browser's date.
 *
 * The popover and its scrim are rendered through a portal into `document.body` (above the
 * assistant dock and clear of any clipping or transformed ancestor), so from 640 px up it is
 * placed from the button's measured position: left edges aligned, or right edges aligned when
 * it would run off the screen (`align` "start" or "end" forces a side), always kept inside the
 * viewport. Below 640 px it is a bottom sheet and the page behind does not scroll while it is
 * open. Focus is not trapped (no `aria-modal`): Tab from the button enters the popover, and
 * Tab past its last control (or a click elsewhere) closes it and moves on.
 */
export default function DateRangeFilter({
  value,
  onChange,
  testId,
  label = "Date range",
  today,
  align,
}: {
  value: DayRange;
  onChange: (range: DayRange) => void;
  testId?: string;
  label?: string;
  today?: string;
  align?: "start" | "end";
}) {
  const [open, setOpen] = useState(false);
  const [cursor, setCursor] = useState("");
  // True between the first and the second click of a range.
  const [picking, setPicking] = useState(false);
  const wrapperRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);
  const scrimRef = useRef<HTMLDivElement>(null);
  const focusCursor = useRef(false);
  const popoverId = useId();
  const id = (suffix: string) => (testId ? `${testId}-${suffix}` : undefined);

  // Keyboard moves and opening put real focus on the day under the cursor, after the render
  // that shows it.
  useEffect(() => {
    if (!open || !focusCursor.current) return;
    focusCursor.current = false;
    popoverRef.current?.querySelector<HTMLElement>(`[data-day="${cursor}"]`)?.focus();
  });

  // The popover lives in document.body, so "inside" means the button wrapper or the portal.
  const inside = useCallback(
    (node: Node) =>
      [wrapperRef, popoverRef, scrimRef].some((ref) => ref.current?.contains(node) ?? false),
    [],
  );

  // Place the popover (from 640 px up; the sheet below is positioned by CSS alone).
  const place = useCallback(() => {
    const popover = popoverRef.current;
    const trigger = buttonRef.current;
    if (!popover || !trigger) return;
    const rect = trigger.getBoundingClientRect();
    const width = popover.offsetWidth;
    const height = popover.offsetHeight;
    let left = align === "end" ? rect.right - width : rect.left;
    if (align === undefined && rect.left + width > window.innerWidth - EDGE) left = rect.right - width;
    left = Math.max(EDGE, Math.min(left, window.innerWidth - width - EDGE));
    // Below the button (the way a menu opens, and it leaves the page title in view) when the
    // whole calendar fits or at least ROOM_BELOW of it shows, scrolling inside the rest
    // (`--fs-date-max`); else above when it fits whole; else the roomier side.
    const natural = Math.max(height, popover.scrollHeight);
    const below = window.innerHeight - EDGE - (rect.bottom + EDGE);
    const above = rect.top - 2 * EDGE;
    let top = rect.bottom + EDGE;
    let room = below;
    if (natural > below && below < ROOM_BELOW && (natural <= above || above > below)) {
      room = above;
      top = rect.top - EDGE - Math.min(natural, above);
    }
    popover.style.setProperty("--fs-date-left", `${left}px`);
    popover.style.setProperty("--fs-date-top", `${top}px`);
    popover.style.setProperty("--fs-date-max", `${Math.max(room, 0)}px`);
  }, [align]);

  // Before paint, and again after every render while open (a six-week month is taller).
  useLayoutEffect(() => {
    if (open) place();
  });
  useEffect(() => {
    if (!open) return;
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    return () => {
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
    };
  }, [open, place]);

  // Below 640 px the sheet looks modal: keep the page behind it still. Restored on close and
  // on unmount; the scrollbar's width is kept as padding so nothing shifts where one is drawn.
  useEffect(() => {
    if (!open) return;
    const query = window.matchMedia("(max-width: 639.98px)");
    const body = document.body;
    const before = { overflow: body.style.overflow, paddingRight: body.style.paddingRight };
    const restore = () => {
      body.style.overflow = before.overflow;
      body.style.paddingRight = before.paddingRight;
    };
    const apply = () => {
      restore();
      if (!query.matches) return;
      const bar = window.innerWidth - document.documentElement.clientWidth;
      body.style.overflow = "hidden";
      if (bar > 0) body.style.paddingRight = `${bar}px`;
    };
    apply();
    query.addEventListener("change", apply);
    return () => {
      query.removeEventListener("change", apply);
      restore();
    };
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      if (event.target instanceof Node && !inside(event.target)) setOpen(false);
    };
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [open, inside]);

  const todayDay = today ?? todayLocal();
  const close = (returnFocus: boolean) => {
    setOpen(false);
    setPicking(false);
    if (returnFocus) buttonRef.current?.focus();
  };
  const toggle = () => {
    if (open) return close(false);
    setCursor(value.from && parseDay(value.from) ? value.from : todayDay);
    setPicking(false);
    focusCursor.current = true;
    setOpen(true);
  };
  const move = (day: string) => {
    focusCursor.current = true;
    setCursor(day);
  };
  const pick = (day: string) => {
    onChange(picking ? selectDay(value, day) : { from: day, to: day });
    setPicking(!picking);
    setCursor(day);
  };

  // React sends events from the portal up to this wrapper, so it hears the popover's keys too.
  const onWrapperKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (!open) return;
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      close(true);
      return;
    }
    if (event.key !== "Tab") return;
    const trigger = buttonRef.current;
    const popover = popoverRef.current;
    const target = event.target as HTMLElement;
    if (!trigger || !popover) return;
    const controls = [...popover.querySelectorAll<HTMLElement>('button:not([tabindex="-1"])')];
    if (target === trigger && !event.shiftKey) {
      // The popover is last in the page, so walk into it from here.
      event.preventDefault();
      controls[0]?.focus();
    } else if (popover.contains(target) && event.shiftKey && target === controls[0]) {
      event.preventDefault();
      trigger.focus();
    } else if (popover.contains(target) && !event.shiftKey && target === controls.at(-1)) {
      // Past the last control: close and carry on after the button, not off the end of the page.
      event.preventDefault();
      close(false);
      const order = [...document.querySelectorAll<HTMLElement>(FOCUSABLE)].filter(
        (el) => !popover.contains(el) && el.tabIndex >= 0 && el.getClientRects().length > 0,
      );
      order[order.indexOf(trigger) + 1]?.focus();
    }
  };
  const onGridKeyDown = (event: KeyboardEvent<HTMLTableElement>) => {
    if (event.altKey || event.ctrlKey || event.metaKey) return;
    const from = (event.target as HTMLElement).closest<HTMLElement>("[data-day]")?.dataset.day;
    if (!from) return;
    const column = weekdayIndex(from);
    const next: Record<string, string> = {
      ArrowLeft: addDays(from, -1),
      ArrowRight: addDays(from, 1),
      ArrowUp: addDays(from, -7),
      ArrowDown: addDays(from, 7),
      Home: addDays(from, -column),
      End: addDays(from, 6 - column),
      PageUp: addMonths(from, event.shiftKey ? -12 : -1),
      PageDown: addMonths(from, event.shiftKey ? 12 : 1),
    };
    const target = next[event.key];
    if (!target) return;
    event.preventDefault();
    move(target);
  };
  const onWrapperBlur = (event: FocusEvent<HTMLDivElement>) => {
    const to = event.relatedTarget;
    if (open && to instanceof Node && !inside(to)) close(false);
  };

  const view = parseDay(cursor) ?? parseDay(todayDay) ?? { year: 1970, month: 1, day: 1 };
  const lo = value.from && value.to ? (compareDays(value.from, value.to) <= 0 ? value.from : value.to) : (value.from ?? value.to);
  const hi = value.from && value.to ? (compareDays(value.from, value.to) <= 0 ? value.to : value.from) : lo;
  const inRange = (day: string) => lo !== null && hi !== null && compareDays(day, lo) >= 0 && compareDays(day, hi) <= 0;
  const edge = (day: string) => day === lo || day === hi;
  const title = monthLabel(view.year, view.month);

  return (
    <div ref={wrapperRef} className="relative inline-block" onKeyDown={onWrapperKeyDown} onBlur={onWrapperBlur}>
      <button
        ref={buttonRef}
        type="button"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={open ? popoverId : undefined}
        data-testid={testId}
        onClick={toggle}
        className={`${smallButton} max-w-full`}
      >
        <svg
          aria-hidden="true"
          viewBox="0 0 24 24"
          width="16"
          height="16"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          className="shrink-0"
        >
          <rect x="3" y="5" width="18" height="16" rx="2" />
          <path d="M3 10h18M8 3v4M16 3v4" />
        </svg>
        <span className={sizeSm}>
          <span className="sr-only">{label}: </span>
          {describeRange(value)}
        </span>
        <Chevron direction="down" />
      </button>
      {open
        ? createPortal(
        <>
          <div
            ref={scrimRef}
            aria-hidden="true"
            data-testid={id("scrim")}
            onClick={() => close(false)}
            className="fixed inset-0 z-50 bg-[var(--fs-scrim)] sm:hidden"
          />
          <div
            ref={popoverRef}
            id={popoverId}
            role="dialog"
            aria-label={label}
            data-testid={id("popover")}
            className={
              "fixed inset-x-0 bottom-0 z-50 max-h-[85dvh] overflow-y-auto rounded-t-[var(--fs-radius-dialog)] border " +
              "border-[color:var(--fs-border)] bg-[var(--fs-surface)] p-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] " +
              "text-[color:var(--fs-text)] shadow-[var(--fs-shadow-popover)] " +
              "sm:inset-x-auto sm:bottom-auto sm:left-(--fs-date-left) sm:top-(--fs-date-top) sm:w-80 " +
              "sm:max-h-(--fs-date-max) sm:rounded-[var(--fs-radius-panel)]"
            }
          >
            <div className="flex items-center justify-between gap-2">
              <button
                type="button"
                aria-label="Previous month"
                data-testid={id("prev")}
                onClick={() => setCursor(addMonths(cursor, -1))}
                className={`${smallButton} min-w-10 px-0`}
              >
                <Chevron direction="left" />
              </button>
              <div aria-live="polite" data-testid={id("month")} className="min-w-0 text-center">
                <span className="text-[length:var(--fs-text-base)] font-semibold">{title}</span>
              </div>
              <button
                type="button"
                aria-label="Next month"
                data-testid={id("next")}
                onClick={() => setCursor(addMonths(cursor, 1))}
                className={`${smallButton} min-w-10 px-0`}
              >
                <Chevron direction="right" />
              </button>
            </div>

            <table
              role="grid"
              aria-label={title}
              data-testid={id("grid")}
              onKeyDown={onGridKeyDown}
              className="mt-2 w-full table-fixed border-collapse"
            >
              <thead>
                <tr>
                  {WEEKDAY_NAMES.map((name) => (
                    <th key={name} scope="col" abbr={name} className="pb-1 text-center">
                      <span className="text-[length:var(--fs-text-xs)] font-medium text-[color:var(--fs-text-secondary)]">
                        {name.slice(0, 2)}
                      </span>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {monthGrid(view.year, view.month).map((row, index) => (
                  <tr key={index}>
                    {row.map((day, column) => {
                      if (!day) return <td key={column} role="gridcell" />;
                      const chosen = inRange(day);
                      const end = chosen && edge(day);
                      const isToday = day === todayDay;
                      const tone = end
                        ? "border-[color:var(--fs-primary)] bg-[var(--fs-primary)] text-[color:var(--fs-on-primary)]"
                        : chosen
                          ? "border-[color:var(--fs-primary)] bg-[var(--fs-surface-blue)] text-[color:var(--fs-text)]"
                          : "border-transparent text-[color:var(--fs-text)] hover:bg-[var(--fs-surface-blue)]";
                      return (
                        <td key={column} role="gridcell" aria-selected={chosen} className="p-0.5 text-center">
                          <button
                            type="button"
                            tabIndex={day === cursor ? 0 : -1}
                            data-day={day}
                            data-today={isToday ? "true" : undefined}
                            data-testid={id(`day-${day}`)}
                            aria-label={isToday ? `${dayLabel(day)}, today` : dayLabel(day)}
                            onClick={() => pick(day)}
                            className={`flex h-10 w-full items-center justify-center rounded-[var(--fs-radius-control)] border-2 pointer-coarse:h-11 sm:pointer-fine:h-9 ${tone}`}
                          >
                            <span
                              className={`text-[length:var(--fs-text-md)] tabular-nums ${end ? "font-bold" : "font-medium"} ${isToday ? "underline decoration-2 underline-offset-4" : ""}`}
                            >
                              {Number(day.slice(8))}
                            </span>
                          </button>
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>

            <p aria-live="polite" data-testid={id("summary")} className="mt-2 text-[length:var(--fs-text-sm)]">
              <span className="font-semibold">{describeRange(value)}</span>
              <span className="text-[color:var(--fs-text-secondary)]">
                {picking ? ". Now pick the last day." : ". Pick a first day, then a last day."}
              </span>
            </p>

            <div className="mt-2 flex flex-wrap gap-2">
              {presets(todayDay).map((preset) => (
                <button
                  key={preset.key}
                  type="button"
                  data-testid={id(`preset-${preset.key}`)}
                  onClick={() => {
                    onChange(preset.range);
                    setPicking(false);
                    const shown = preset.range.to ?? preset.range.from;
                    if (shown) setCursor(shown);
                  }}
                  className={smallButton}
                >
                  <span className={sizeSm}>{preset.label}</span>
                </button>
              ))}
              <button
                type="button"
                data-testid={id("done")}
                onClick={() => close(true)}
                className={
                  "ml-auto inline-flex min-h-10 items-center justify-center rounded-[var(--fs-radius-control)] border border-[color:var(--fs-primary)] " +
                  "bg-[var(--fs-primary)] px-4 text-[color:var(--fs-on-primary)] pointer-coarse:min-h-11"
                }
              >
                <span className={sizeSm}>Done</span>
              </button>
            </div>
          </div>
        </>,
        document.body,
      )
        : null}
    </div>
  );
}
