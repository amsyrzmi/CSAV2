"use client";

import type { ReactNode } from "react";
import {
  orderStatusLabel,
  orderStatusTone,
  orderSummary,
  paymentLabel,
  paymentTone,
  productSummary,
  storeStatusTone,
  storeSummary,
  whenText,
  type Tone,
} from "@/lib/business-view";
import StatusLabel from "../fs/StatusLabel";
import { DetailsRow } from "../kit";

export type Order = {
  id: string; status: string; payment_status: string; total: string; currency: string;
  channel: string; external_order_id: string | null; external_shop_id: string | null;
  tracking_no: string | null; carrier: string | null; placed_at: string | null;
  shipped_at: string | null; created_at: string; updated_at: string;
};
export type Product = {
  id: string; sku: string; name: string; price: string; currency: string;
  stock_qty: number; reserved_qty: number; available_qty: number;
  is_active: boolean; updated_at: string;
};
export type StoreSummaryInput = { platform: string; shop_id: string | null; connection: { status: string } };

const card =
  "rounded-[var(--fs-radius-panel)] border border-[color:var(--fs-border)] bg-[var(--fs-surface)] p-3";

/** A closed-by-default fold for long technical text: a plain title and the usual arrow. */
export function Fold({ testId, title, children }: { testId: string; title: string; children: ReactNode }) {
  return (
    <div className={card}>
      <DetailsRow
        testId={testId}
        toggleLabel={title}
        summary={<p className="font-medium">{title}</p>}
      >
        {children}
      </DetailsRow>
    </div>
  );
}

/** A bordered card: the plain summary on the left, the arrow on the right, the record behind it. */
function RecordCard({
  testId, toggleLabel, title, subtitle, labels, children,
}: {
  testId: string;
  toggleLabel: string;
  title: string;
  subtitle: string;
  labels: { testId: string; tone: Tone; text: string }[];
  children: ReactNode;
}) {
  return (
    <li className={card}>
      <DetailsRow
        testId={testId}
        toggleLabel={toggleLabel}
        summary={
          <div className="min-w-0">
            <p data-testid={`${testId}-title`} className="font-medium">{title}</p>
            <p className="text-sm text-[color:var(--fs-text-secondary)]">{subtitle}</p>
            <div className="mt-1.5 flex flex-wrap gap-1.5">
              {labels.map((label) => (
                <span key={label.testId} data-testid={`${testId}-${label.testId}`}>
                  <StatusLabel tone={label.tone}>{label.text}</StatusLabel>
                </span>
              ))}
            </div>
          </div>
        }
      >
        {children}
      </DetailsRow>
    </li>
  );
}

/** An order from the fields the order read returns; the original lines are kept in the details. */
export function OrderCard({ order }: { order: Order }) {
  const summary = orderSummary(order);
  return (
    <RecordCard
      testId={`business-order-${order.id}`}
      toggleLabel={summary.title}
      title={summary.title}
      subtitle={summary.subtitle}
      labels={[
        { testId: "status", tone: orderStatusTone(order.status), text: orderStatusLabel(order.status) },
        { testId: "payment", tone: paymentTone(order.payment_status), text: paymentLabel(order.payment_status) },
      ]}
    >
      <p className="break-all font-semibold">{order.id}</p>
      <p>{order.status} · Payment: {order.payment_status} · {order.currency} {order.total}</p>
      <p>Source: {order.channel} · Store: {order.external_shop_id ?? "Not recorded"}</p>
      <p>Platform order: {order.external_order_id ?? "Not recorded"}</p>
      <p>Tracking: {order.tracking_no ?? "Not recorded"} · Carrier: {order.carrier ?? "Not recorded"}</p>
      <p>Created: {whenText(order.created_at)} · Placed: {whenText(order.placed_at)} · Shipped: {whenText(order.shipped_at)}</p>
      <p>Local update: {new Date(order.updated_at).toLocaleString()} — not a marketplace sync timestamp</p>
    </RecordCard>
  );
}

export function ProductCard({ product }: { product: Product }) {
  const summary = productSummary(product);
  return (
    <RecordCard
      testId={`business-product-${product.id}`}
      toggleLabel={`product ${product.name}`}
      title={summary.title}
      subtitle={summary.subtitle}
      labels={[{
        testId: "status",
        tone: product.is_active ? "success" : "neutral",
        text: product.is_active ? "Active" : "Inactive",
      }]}
    >
      <p className="break-words font-semibold">{product.sku} · {product.name}</p>
      <p>{product.currency} {product.price} · Local status: {product.is_active ? "Active" : "Inactive"}</p>
      <p>Stock: {product.stock_qty} · Reserved: {product.reserved_qty} · Available: {product.available_qty}</p>
      <p>Local update: {new Date(product.updated_at).toLocaleString()} — not a marketplace sync timestamp</p>
    </RecordCard>
  );
}

/** The card shell for a local store record; the page supplies its original lines as children. */
export function StoreCard({
  id, store, children,
}: { id: string; store: StoreSummaryInput; children: ReactNode }) {
  const summary = storeSummary(store);
  return (
    <RecordCard
      testId={`business-store-${id}`}
      toggleLabel={`store ${summary.title}`}
      title={summary.title}
      subtitle={summary.subtitle}
      labels={[{ testId: "status", tone: storeStatusTone(store.connection.status), text: summary.status }]}
    >
      {children}
    </RecordCard>
  );
}
