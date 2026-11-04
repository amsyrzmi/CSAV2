"use client";

import { useId, useState, type ReactNode } from "react";
import { paymentOptions } from "@/lib/business-view";
import type { DayRange } from "@/lib/date-range";
import { DateRangeFilter, TechSwitch } from "../kit";
import { field, secondary } from "../ui";

/**
 * The row under the counts: the page's "Show technical details" switch and one Filters button.
 * The button opens a panel with the order date, the payment status and (as `children`) the
 * source filters. The panel stays mounted when closed, so nothing chosen is lost by closing it.
 */
export default function OrderFilters({
  range, onRange, payment, onPayment, children,
}: {
  range: DayRange;
  onRange: (next: DayRange) => void;
  payment: string;
  onPayment: (next: string) => void;
  children?: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const panelId = useId();
  return (
    <>
      <div className="mb-3 flex flex-wrap items-center gap-3">
        <TechSwitch testId="business-tech-all" />
        <button
          type="button"
          className={secondary}
          aria-expanded={open}
          aria-controls={panelId}
          data-testid="business-filters-toggle"
          onClick={() => setOpen((value) => !value)}
        >
          Filters
        </button>
      </div>
      <div id={panelId} data-testid="business-filters" hidden={!open}
        className="mb-3 rounded-[var(--fs-radius-panel)] border border-[color:var(--fs-border)] bg-[var(--fs-surface)] p-3">
        <div className="mb-3 flex flex-wrap items-end gap-3">
          <div className="shrink-0">
            <p aria-hidden="true" className="mb-1 text-sm">Order date</p>
            <DateRangeFilter testId="business-date" label="Order date" value={range} onChange={onRange} />
          </div>
          <div className="min-w-0 flex-1 basis-40 sm:w-64 sm:flex-none">
            <label htmlFor="business-payment" className="mb-1 block text-sm">Payment status</label>
            <select id="business-payment" data-testid="business-payment" className={field} value={payment}
              onChange={(event) => onPayment(event.target.value)}>
              <option value="">Any payment status</option>
              {paymentOptions().map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
            </select>
          </div>
        </div>
        {children}
      </div>
    </>
  );
}
