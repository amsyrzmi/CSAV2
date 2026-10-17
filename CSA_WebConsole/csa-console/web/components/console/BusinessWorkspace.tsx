"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Client, Me } from "@/lib/identity-contracts";
import { field, Heading, Notice, Panel, secondary } from "./ui";

type Order = {
  id: string; status: string; payment_status: string; total: string; currency: string;
  channel: string; external_order_id: string | null; external_shop_id: string | null;
  tracking_no: string | null; carrier: string | null; updated_at: string;
};
type Product = {
  id: string; sku: string; name: string; price: string; currency: string;
  stock_qty: number; reserved_qty: number; available_qty: number;
  is_active: boolean; updated_at: string;
};
type Records<T> = {
  source: string; marketplace_sync: string; items: T[]; next_cursor: string | null;
};

function useRecords<T>(path: string) {
  const [cursor, setCursor] = useState<string | null>(null);
  const [data, setData] = useState<Records<T> | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    api<Records<T>>(`${path}${cursor ? `&before=${encodeURIComponent(cursor)}` : ""}`, {
      signal: AbortSignal.any([controller.signal, AbortSignal.timeout(15_000)]),
    }).then((result) => {
      if (!controller.signal.aborted) setData(result);
    }).catch((cause: Error) => {
      if (!controller.signal.aborted) setError(cause.message);
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false);
    });
    return () => controller.abort();
  }, [path, cursor, revision]);
  function reset() { setLoading(true); setData(null); setError(""); }
  return {
    data, error, loading,
    older: () => { const next = data?.next_cursor; if (next) { reset(); setCursor(next); } },
    newest: () => { reset(); setCursor(null); setRevision((value) => value + 1); },
  };
}

export function BusinessWorkspace({ me }: { me: Me }) {
  const platform = me.role === "platform_admin";
  const permitted = platform || me.role === "tenant_admin" || me.role === "tenant_staff";
  const [clients, setClients] = useState<Client[]>([]);
  const [selected, setSelected] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    if (!platform) return;
    const controller = new AbortController();
    api<Client[]>("/console/v1/admin/clients", { signal: controller.signal })
      .then(setClients)
      .catch((cause: Error) => { if (!controller.signal.aborted) setError(cause.message); });
    return () => controller.abort();
  }, [platform]);
  if (!permitted) return <Notice error>Business records require an administrator or staff role.</Notice>;
  const tenant = platform ? selected : me.active_client_id;
  return <>
    <Heading title="Business management" description="Read-only local orders, tracking references, products and stock." />
    <Notice>Local records only, not a live TikTok Shop feed. No marketplace synchronization or store
      connection verification is performed. Stock is not synchronized or authoritative across stores.
      Refresh explicitly to read current local records.</Notice>
    {error && <Notice error>{error}</Notice>}
    {platform && <label className="my-5 block">Tenant
      <select className={field} value={selected} onChange={(event) => setSelected(event.target.value)}>
        <option value="">Select a tenant</option>
        {clients.map((client) => <option key={client.id} value={client.id}>{client.name}</option>)}
      </select>
    </label>}
    {tenant ? <BusinessRecords key={tenant} query={platform ? `client_id=${encodeURIComponent(tenant)}` : ""} />
      : <Notice>Select a tenant to view its business records.</Notice>}
    <div className="mt-5"><Panel title="Not available in this slice">
      <p className="text-sm">Marketplace listings, store connections, live carrier tracking, listing edits,
        stock updates, fulfilment, cancellations, returns/refunds, payments and cross-platform synchronization
        are unavailable here. Tickets, invoices, PDF downloads and staff preparation are later M6 slices.
        Existing adapter code does not establish live seller authorization or a verified connection.</p>
    </Panel></div>
  </>;
}

function BusinessRecords({ query }: { query: string }) {
  const orders = useRecords<Order>(`/console/v1/business/orders?${query}`);
  const products = useRecords<Product>(`/console/v1/business/products?${query}`);
  return <div className="mt-5 space-y-5">
    <Panel title="Orders and tracking references">
      <div className="mb-4 flex gap-3">
        <button className={secondary} disabled={orders.loading} onClick={orders.newest}>Refresh newest orders</button>
        {orders.data?.next_cursor && <button className={secondary} disabled={orders.loading} onClick={orders.older}>Older orders</button>}
      </div>
      {orders.error && <Notice error>{orders.error}</Notice>}
      {orders.loading && <p>Loading local orders...</p>}
      {orders.data?.items.length === 0 && <p>No local orders found. This does not mean your marketplace has no orders.</p>}
      <ul className="space-y-3">{orders.data?.items.map((order) => <li key={order.id} className="rounded-md border border-ink-200 p-3 text-sm">
        <h3 className="break-all font-semibold">{order.id}</h3>
        <p>{order.status} · Payment: {order.payment_status} · {order.currency} {order.total}</p>
        <p>Source: {order.channel} · Store: {order.external_shop_id ?? "Not recorded"}</p>
        <p>Platform order: {order.external_order_id ?? "Not recorded"}</p>
        <p>Tracking: {order.tracking_no ?? "Not recorded"} · Carrier: {order.carrier ?? "Not recorded"}</p>
        <p>Local update: {new Date(order.updated_at).toLocaleString()} — not a marketplace sync timestamp</p>
      </li>)}</ul>
    </Panel>
    <Panel title="Local catalogue and inventory">
      <div className="mb-4 flex gap-3">
        <button className={secondary} disabled={products.loading} onClick={products.newest}>Refresh newest products</button>
        {products.data?.next_cursor && <button className={secondary} disabled={products.loading} onClick={products.older}>Older products</button>}
      </div>
      {products.error && <Notice error>{products.error}</Notice>}
      {products.loading && <p>Loading local products...</p>}
      {products.data?.items.length === 0 && <p>No local products found. Marketplace listings are not imported by this view.</p>}
      <ul className="space-y-3">{products.data?.items.map((product) => <li key={product.id} className="rounded-md border border-ink-200 p-3 text-sm">
        <h3 className="break-words font-semibold">{product.sku} · {product.name}</h3>
        <p>{product.currency} {product.price} · Local status: {product.is_active ? "Active" : "Inactive"}</p>
        <p>Stock: {product.stock_qty} · Reserved: {product.reserved_qty} · Available: {product.available_qty}</p>
        <p>Local update: {new Date(product.updated_at).toLocaleString()} — not a marketplace sync timestamp</p>
      </li>)}</ul>
    </Panel>
  </div>;
}
