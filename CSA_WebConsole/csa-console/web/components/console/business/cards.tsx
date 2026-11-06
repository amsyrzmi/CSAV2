"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import type { Order } from "@/lib/business";
import {
  detailRequested,
  isSyncedOrder,
  orderBadge,
  orderDetailGroups,
  orderStatusLabel,
  orderStatusTone,
  orderSummary,
  orderSyncText,
  orderUpdateLine,
  paymentLabel,
  paymentTone,
  productDetailGroups,
  productSummary,
  providerStatusLabel,
  storeDetailGroups,
  storeStatusTone,
  storeSummary,
  type DetailGroup,
  whenText,
  type Tone,
} from "@/lib/business-view";
import StatusLabel from "../fs/StatusLabel";
import { DetailsRow, TechnicalDetails, useTechOpen } from "../kit";
import OrderDetailPanel from "./OrderDetail";

export type { Order };
export type Product = {
  id: string; sku: string; name: string; price: string; currency: string;
  stock_qty: number; reserved_qty: number; available_qty: number;
  is_active: boolean; updated_at: string;
};
export type StoreSummaryInput = {
  platform: string; shop_id: string | null; updated_at: string; connection: { status: string };
};

const card =
  "rounded-[var(--fs-radius-panel)] border border-[color:var(--fs-border)] bg-[var(--fs-surface)] p-3";

/** A closed-by-default fold for long technical text: a plain title and the usual arrow. */
export function Fold({ testId, title, hint, children }: {
  testId: string; title: string; hint?: string; children: ReactNode;
}) {
  return (
    <div className={card}>
      <DetailsRow
        testId={testId}
        toggleLabel={title}
        summary={<p className="font-medium">{title}{hint && <span
          data-testid={`${testId}-hint`} className="ml-2 font-normal text-[color:var(--fs-text-secondary)]">· {hint}</span>}</p>}
      >
        {children}
      </DetailsRow>
    </div>
  );
}

/** Plain labelled groups, each a heading and a list of label/value pairs. */
function DetailGroups({ groups }: { groups: DetailGroup[] }) {
  return (
    <div className="space-y-3">
      {groups.map((group) => {
        // A group whose only row says the same word as its title needs no second heading.
        const bare = group.rows.length === 1 && group.rows[0].label === group.title;
        return (
          <div key={group.title}>
            {bare ? null : <h4 className="mb-1 font-semibold">{group.title}</h4>}
            <dl className="space-y-0.5">
              {group.rows.map((row) => (
                <div key={row.label} className="flex flex-wrap gap-x-2">
                  <dt className="text-[color:var(--fs-text-secondary)]">{row.label}:</dt>{" "}
                  <dd className="min-w-0">{row.value}</dd>
                </div>
              ))}
            </dl>
          </div>
        );
      })}
    </div>
  );
}

/**
 * A bordered card: the plain summary on the left, the arrow on the right. Behind the arrow come
 * plain labelled groups and then a second arrow, Technical details, holding the exact lines
 * (`children`). Both arrows follow the page's "Show technical details" switch.
 */
export function RecordCard({
  testId, toggleLabel, title, subtitle, labels, groups, extra, detail, onOpen, children,
}: {
  testId: string;
  toggleLabel: string;
  title: string;
  subtitle: string;
  /** `fullTestId` replaces the generated `${testId}-${testId of the label}`. */
  labels: { testId: string; tone: Tone; text: string; fullTestId?: string }[];
  groups: DetailGroup[];
  /** Plain lines and buttons under the labels; they stay usable while the details are closed. */
  extra?: ReactNode;
  /** Extra detail between the plain groups and Technical details; told whether a person opened the card
   * (not whether the page's technical switch did). */
  detail?: (open: boolean) => ReactNode;
  /** Told when a person opens the card (true) and when it closes or goes away (false). */
  onOpen?: (open: boolean) => void;
  children: ReactNode;
}) {
  const [open, setOpen] = useTechOpen();
  // Whether a person opened this card. The page's "Show technical details" switch opens every
  // card but is not that: it must not read an order's ship-to or publish a record to the assistant.
  const [userOpen, setUserOpen] = useState(false);
  if (userOpen && !open) setUserOpen(false);
  const asked = detailRequested(open, userOpen);
  // The callback is kept in a ref so a new function each render does not re-run the effect.
  const told = useRef(onOpen);
  useEffect(() => { told.current = onOpen; });
  useEffect(() => {
    if (!asked) return;
    told.current?.(true);
    return () => told.current?.(false);
  }, [asked]);
  return (
    <li className={card}>
      <DetailsRow
        testId={testId}
        toggleLabel={toggleLabel}
        open={open}
        onOpenChange={(next) => { setOpen(next); setUserOpen(next); }}
        summary={
          <div className="min-w-0">
            <p data-testid={`${testId}-title`} className="font-medium">{title}</p>
            <p className="text-sm text-[color:var(--fs-text-secondary)]">{subtitle}</p>
            <div className="mt-1.5 flex flex-wrap gap-1.5">
              {labels.map((label) => (
                <span key={label.testId} data-testid={label.fullTestId ?? `${testId}-${label.testId}`}>
                  <StatusLabel tone={label.tone}>{label.text}</StatusLabel>
                </span>
              ))}
            </div>
            {extra}
          </div>
        }
      >
        <DetailGroups groups={groups} />
        {detail?.(asked)}
        <TechnicalDetails testId={`${testId}-tech`}>{children}</TechnicalDetails>
      </DetailsRow>
    </li>
  );
}

/**
 * An order from the fields the order read returns; the original lines are kept in the details.
 * A TikTok order carries the TikTok badge, and one copied by sync also TikTok's own status, when it
 * was synced, and a detail that reads the order's lines, totals and ship-to when it is opened.
 */
export function OrderCard({
  order, query, now, onOpen,
}: {
  order: Order;
  /** The platform admin's tenant query (`client_id=...`), empty otherwise. */
  query: string;
  now: Date;
  /** Told when this card's details open and close, so the page can publish the open order. */
  onOpen?: (id: string, open: boolean) => void;
}) {
  const summary = orderSummary(order);
  const badge = orderBadge(order);
  const synced = isSyncedOrder(order);
  const syncText = orderSyncText(order, now);
  const toldPage = onOpen ? (open: boolean) => onOpen(order.id, open) : undefined;
  return (
    <RecordCard
      testId={`business-order-${order.id}`}
      toggleLabel={summary.title}
      title={summary.title}
      subtitle={summary.subtitle}
      labels={[
        ...(badge ? [{ testId: "badge", fullTestId: `business-order-badge-${order.id}`, tone: "info" as Tone, text: badge }] : []),
        { testId: "status", tone: orderStatusTone(order.status), text: orderStatusLabel(order.status) },
        ...(synced && order.provider_status
          ? [{ testId: "provider-status", tone: "neutral" as Tone, text: `TikTok: ${providerStatusLabel(order.provider_status)}` }]
          : []),
        { testId: "payment", tone: paymentTone(order.payment_status), text: paymentLabel(order.payment_status) },
      ]}
      groups={orderDetailGroups(order)}
      extra={syncText && <p data-testid={`business-order-${order.id}-sync`}
        className="mt-1.5 text-sm text-[color:var(--fs-text-secondary)]">{syncText}</p>}
      detail={synced ? (open) => <OrderDetailPanel order={order} query={query} open={open} /> : undefined}
      onOpen={toldPage}
    >
      <p className="break-all font-semibold">{order.id}</p>
      <p>{order.status} · Payment: {order.payment_status} · {order.currency} {order.total}</p>
      <p>Source: {order.channel} · Store: {order.external_shop_id ?? "Not recorded"}</p>
      <p>Platform order: {order.external_order_id ?? "Not recorded"}</p>
      <p>Tracking: {order.tracking_no ?? "Not recorded"} · Carrier: {order.carrier ?? "Not recorded"}</p>
      <p>Created: {whenText(order.created_at)} · Placed: {whenText(order.placed_at)} · Shipped: {whenText(order.shipped_at)}</p>
      <p>{orderUpdateLine(order)}</p>
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
      groups={productDetailGroups(product)}
    >
      <p className="break-words font-semibold">{product.sku} · {product.name}</p>
      <p>{product.currency} {product.price} · Local status: {product.is_active ? "Active" : "Inactive"}</p>
      <p>Stock: {product.stock_qty} · Reserved: {product.reserved_qty} · Available: {product.available_qty}</p>
      <p>Local update: {whenText(product.updated_at)} — not a marketplace sync timestamp</p>
    </RecordCard>
  );
}

/** The card shell for a store; the page supplies its sync lines and buttons as `extra` and its exact lines as children. */
export function StoreCard({
  id, store, extra, children,
}: { id: string; store: StoreSummaryInput; extra?: ReactNode; children: ReactNode }) {
  const summary = storeSummary(store);
  return (
    <RecordCard
      testId={`business-store-${id}`}
      toggleLabel={`store ${summary.title}`}
      title={summary.title}
      subtitle={summary.subtitle}
      labels={[{ testId: "status", tone: storeStatusTone(store.connection.status), text: summary.status }]}
      groups={storeDetailGroups(store)}
      extra={extra}
    >
      {children}
    </RecordCard>
  );
}
