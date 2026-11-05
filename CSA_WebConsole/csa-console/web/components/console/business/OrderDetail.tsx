"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { orderDetailPath, type Order, type OrderDetail } from "@/lib/business";
import { orderDetailView } from "@/lib/business-view";
import { secondary } from "../ui";

type Read = { id: string; detail: OrderDetail | null; error: string };

/**
 * The detail read of one synced order (`GET /orders/{id}`), started the first time its card opens
 * and kept after that. Loading is derived (open, nothing yet, no error) so no state is set while
 * rendering. `retry` starts the read again after a failure.
 */
function useOrderDetail(id: string, query: string, enabled: boolean) {
  const [read, setRead] = useState<Read>({ id, detail: null, error: "" });
  const [attempt, setAttempt] = useState(0);
  const current = read.id === id ? read : { id, detail: null, error: "" };
  const wanted = enabled && current.detail === null && current.error === "";
  useEffect(() => {
    if (!wanted) return;
    const controller = new AbortController();
    api<OrderDetail>(orderDetailPath(query, id), {
      signal: AbortSignal.any([controller.signal, AbortSignal.timeout(15_000)]),
    }).then((detail) => {
      if (!controller.signal.aborted) setRead({ id, detail, error: "" });
    }).catch((cause: Error) => {
      if (!controller.signal.aborted) setRead({ id, detail: null, error: cause.message || "failed" });
    });
    return () => controller.abort();
  }, [wanted, id, query, attempt]);
  return {
    detail: current.detail,
    error: current.error,
    loading: wanted,
    retry: () => { setRead({ id, detail: null, error: "" }); setAttempt((value) => value + 1); },
  };
}

const heading = "mb-1 font-semibold";

/** Lines, the totals breakdown and the ship-to of a TikTok order, as TikTok gave them. */
export default function OrderDetailPanel({ order, query, open }: { order: Order; query: string; open: boolean }) {
  const { detail, error, loading, retry } = useOrderDetail(order.id, query, open);
  const testId = `business-order-detail-${order.id}`;
  if (!open && detail === null) return null;
  if (error) {
    return <div data-testid={testId} className="mt-3 space-y-2">
      <p role="alert" data-testid={`${testId}-error`} className="text-[color:var(--fs-danger)]">
        We couldn&apos;t load this order&apos;s items and delivery details. Try again in a moment.
      </p>
      <button type="button" className={secondary} onClick={retry}>Try again</button>
    </div>;
  }
  if (detail === null) {
    return loading ? <p data-testid={`${testId}-loading`} role="status" className="mt-3">
      Loading the items and delivery details...</p> : null;
  }
  const view = orderDetailView(detail);
  return <div data-testid={testId} className="mt-3 space-y-3">
    <div>
      <h4 className={heading}>Items</h4>
      {view.lines.length === 0
        ? <p>TikTok didn&apos;t list any items for this order.</p>
        : <ul data-testid={`${testId}-lines`} className="list-disc space-y-0.5 pl-5">
            {view.lines.map((line) => <li key={line.key}>{line.text}</li>)}
          </ul>}
    </div>
    <div>
      <h4 className={heading}>Totals</h4>
      <dl data-testid={`${testId}-totals`} className="space-y-0.5">
        {view.totals.map((row) => <div key={row.label} className="flex flex-wrap gap-x-2">
          <dt className="text-[color:var(--fs-text-secondary)]">{row.label}:</dt>{" "}
          <dd className="min-w-0">{row.value}</dd>
        </div>)}
      </dl>
    </div>
    <div>
      <h4 className={heading}>Ship to</h4>
      {view.clearedNote
        ? <p data-testid={`${testId}-cleared`}>{view.clearedNote}</p>
        : view.shipTo && view.shipTo.length > 0
          ? <dl data-testid={`${testId}-ship-to`} className="space-y-0.5">
              {view.shipTo.map((row) => <div key={row.label} className="flex flex-wrap gap-x-2">
                <dt className="text-[color:var(--fs-text-secondary)]">{row.label}:</dt>{" "}
                <dd className="min-w-0">{row.value}</dd>
              </div>)}
            </dl>
          : <p data-testid={`${testId}-no-ship-to`}>TikTok didn&apos;t share a delivery address for this order.</p>}
    </div>
  </div>;
}
