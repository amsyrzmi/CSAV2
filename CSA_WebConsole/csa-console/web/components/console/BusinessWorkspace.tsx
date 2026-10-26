"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import {
  appendDemoListing,
  connectionLabels,
  demoListings,
  localOrderPath,
  type ConnectionStatus,
  type DemoListing,
} from "@/lib/business";
import type { Client, Me } from "@/lib/identity-contracts";
import { usePublishContext } from "@/lib/page-context";
import { action, field, Heading, Notice, Panel, secondary } from "./ui";

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
type Store = {
  id: string; platform: string; shop_id: string | null; expires_at: string | null;
  updated_at: string; expiry: "unknown" | "expired" | "not_expired";
  authorization: "unverified"; permissions: "unknown"; provider_reads: "unavailable";
  connection: { status: ConnectionStatus; live_integration: "unavailable" };
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
  usePublishContext({ tab: "business" });
  const platform = me.role === "platform_admin";
  const permitted = platform || me.role === "tenant_admin" || me.role === "tenant_staff";
  const [clients, setClients] = useState<Client[]>([]);
  const [selected, setSelected] = useState("");
  const [error, setError] = useState("");
  const [demoMode, setDemoMode] = useState(false);
  useEffect(() => {
    if (!platform || demoMode) return;
    const controller = new AbortController();
    api<Client[]>("/console/v1/admin/clients", { signal: controller.signal })
      .then(setClients)
      .catch((cause: Error) => { if (!controller.signal.aborted) setError(cause.message); });
    return () => controller.abort();
  }, [platform, demoMode]);
  if (!permitted) return <Notice error>Business records require an administrator or staff role.</Notice>;
  const tenant = platform ? selected : me.active_client_id;
  return <>
    <Heading
      title="Business management"
      description={demoMode
        ? "Synthetic demo only — sample listings do not connect to a seller account."
        : "Read-only local orders, tracking references, products and stock."}
    />
    <div className="mb-5">
      <button
        className={secondary}
        type="button"
        aria-pressed={demoMode}
        onClick={() => setDemoMode((value) => !value)}
      >
        {demoMode ? "Exit demo mode" : "Show synthetic demo"}
      </button>
    </div>
    {demoMode
      ? <Notice>DEMO MODE — fictional sample data only. Not connected to TikTok; no TikTok API request or
          listing is sent. Demo changes stay in this page&apos;s temporary state and reset when you exit
          or reload.</Notice>
      : <Notice>Local records only, not a live TikTok Shop feed. No marketplace synchronization or store
          connection verification is performed. Stock is not synchronized or authoritative across stores.
          Refresh explicitly to read current local records.</Notice>}
    {!demoMode && error && <Notice error>{error}</Notice>}
    {!demoMode && platform && <label className="my-5 block">Tenant
      <select className={field} value={selected} onChange={(event) => setSelected(event.target.value)}>
        <option value="">Select a tenant</option>
        {clients.map((client) => <option key={client.id} value={client.id}>{client.name}</option>)}
      </select>
    </label>}
    {demoMode
      ? <DemoBusinessWorkspace />
      : <>
          {tenant ? <BusinessRecords key={tenant} query={platform ? `client_id=${encodeURIComponent(tenant)}` : ""} />
            : <Notice>Select a tenant to view its business records.</Notice>}
          <div className="mt-5"><Panel title="Not available in this slice">
            <p className="text-sm">Marketplace listings, connection verification or changes, live carrier tracking, listing edits,
              stock updates, fulfilment, cancellations, returns/refunds, payments and cross-platform synchronization
              are unavailable here. Tickets, invoices, PDF downloads and staff preparation are later M6 slices.
              Existing adapter code does not establish live seller authorization or a verified connection.</p>
          </Panel></div>
        </>}
  </>;
}

function DemoBusinessWorkspace() {
  const [listings, setListings] = useState<DemoListing[]>(() => [...demoListings]);
  return <div className="mt-5 space-y-5">
    <Panel title="Demo store · sample listings">
      <p className="mb-4 text-sm">These fictional products are not in your TikTok Shop or any local
        catalogue. The button below only appends a sample row in temporary browser memory.</p>
      <button
        className={action}
        type="button"
        onClick={() => setListings((current) => appendDemoListing(current))}
      >
        Simulate create listing
      </button>
      <p className="my-4 text-sm" role="status">No TikTok request was made. Demo listing changes are not saved.</p>
      <ul className="space-y-3">
        {listings.map((listing) => <li key={listing.id} className="rounded-md border border-ink-200 p-3 text-sm">
          <h3 className="font-semibold">{listing.sku} · {listing.name}</h3>
          <p>MYR {listing.price} · {listing.status} · Demo only, not published</p>
        </li>)}
      </ul>
    </Panel>
    <Panel title="Demo connection status">
      <p className="text-sm">No seller account is connected. Partner eligibility, shop authorization and
        live listing permissions have not been verified in this demo.</p>
    </Panel>
  </div>;
}

function BusinessRecords({ query }: { query: string }) {
  const [channel, setChannel] = useState("");
  const [shopId, setShopId] = useState("");
  const [orderPath, setOrderPath] = useState(() => localOrderPath(query, "", ""));
  const products = useRecords<Product>(`/console/v1/business/products?${query}`);
  return <div className="mt-5 space-y-5">
    <Panel title="Local store records (not verified connections)">
      <StoreRecords key={query} query={query} />
    </Panel>
    <Panel title="Orders and tracking references">
      <form className="mb-4 space-y-3" onSubmit={(event) => {
        event.preventDefault();
        setOrderPath(localOrderPath(query, channel, shopId));
      }}>
        <label className="block">Source/platform (local orders)
          <select className={field} value={channel} onChange={(event) => {
            setChannel(event.target.value);
            setShopId("");
          }}>
            <option value="">All local sources</option>
            <option value="tiktok_shop">TikTok Shop</option>
            <option value="shopee">Shopee</option>
            <option value="telegram">Telegram</option>
            <option value="web">Web</option>
            <option value="manual">Manual</option>
          </select>
        </label>
        <label className="block">Exact store ID (optional, requires source/platform)
          <input className={field} value={shopId} maxLength={100} disabled={!channel}
            onChange={(event) => setShopId(event.target.value)} />
        </label>
        <button className={secondary} type="submit">Apply order filters</button>
        <p className="text-sm">Filters match persisted source and store IDs exactly. Options do not
          indicate connected stores. Apply filters to restart at the newest matching orders.</p>
      </form>
      <OrderRecords key={orderPath} path={orderPath} />
    </Panel>
    <Panel title="Local catalogue and inventory">
      <p className="mb-4 text-sm">Tenant-wide local stock. Order source/store filters do not filter
        these products; verified store listing mappings are not available in this slice.</p>
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

function StoreRecords({ query }: { query: string }) {
  const [platform, setPlatform] = useState("");
  const params = new URLSearchParams(query);
  if (platform) params.set("platform", platform);
  const path = `/console/v1/business/stores?${params}`;
  return <>
    <Notice>These are local records and stored lifecycle evidence, not a live connection check.
      Token contents, shop ciphers and permission scope values are not returned. Current provider
      authorization and permissions remain unverified; provider reads are unavailable.
      Recorded authorization or a future expiry does not prove a working connection.</Notice>
    <p className="my-3 text-sm">Live TikTok authorization requires an eligible Partner Center app
      and seller authorization. Account registration alone does not establish eligibility;
      review/publishing or an independently verified testing route is still required.
      Live authorization is not enabled in this workspace.</p>
    <div className="mb-3 flex gap-3">
      <button className={secondary} type="button" disabled>Connect unavailable</button>
      <button className={secondary} type="button" disabled>Reconnect unavailable</button>
    </div>
    <label className="my-3 block">Store-record platform
      <select className={field} value={platform} onChange={(event) => setPlatform(event.target.value)}>
        <option value="">All local marketplace records</option>
        <option value="tiktok_shop">TikTok Shop</option>
        <option value="shopee">Shopee</option>
        <option value="lazada">Lazada</option>
      </select>
    </label>
    <StorePage key={path} path={path} />
  </>;
}

function StorePage({ path }: { path: string }) {
  const stores = useRecords<Store>(path);
  return <>
    <div className="mb-4 flex gap-3">
      <button className={secondary} disabled={stores.loading} onClick={stores.newest}>Refresh local store records</button>
      {stores.data?.next_cursor && <button className={secondary} disabled={stores.loading} onClick={stores.older}>Next store records</button>}
    </div>
    <p className="mb-3 text-sm">Pages follow stable local record IDs, not connection or update recency.</p>
    {stores.error && <Notice error>{stores.error}</Notice>}
    {stores.loading && <p>Loading local store records...</p>}
    {stores.data?.items.length === 0 && <p>No matching local store records. This does not prove the marketplace has no authorized stores.</p>}
    <ul className="space-y-3">{stores.data?.items.map((store) => <li key={store.id} className="rounded-md border border-ink-200 p-3 text-sm">
      <h3 className="break-all font-semibold">{store.platform} · {store.shop_id ?? "Store ID not recorded"}</h3>
      <p className="break-all">Local record: {store.id}</p>
      <p>Stored lifecycle: {connectionLabels[store.connection.status]}</p>
      <p>Live integration: {store.connection.live_integration}</p>
      <p>Authorization: {store.authorization} · Permissions: {store.permissions} · Provider reads: {store.provider_reads}</p>
      <p>Recorded expiry: {store.expires_at ? new Date(store.expires_at).toLocaleString() : "Unknown"}
        {" · "}{store.expiry === "not_expired" ? "Not expired by recorded timestamp only" : store.expiry === "expired" ? "Expired by recorded timestamp" : "Expiry unknown"}</p>
      <p>Local update: {new Date(store.updated_at).toLocaleString()} — not a provider verification timestamp</p>
    </li>)}</ul>
  </>;
}

function OrderRecords({ path }: { path: string }) {
  const orders = useRecords<Order>(path);
  const active = new URL(path, "http://local.invalid").searchParams;
  return <>
    <p className="mb-3 text-sm">Applied source: {active.get("channel") ?? "All local sources"}
      {" · "}Applied store ID: {active.get("shop_id") ?? "All stores / not recorded"}</p>
    <div className="mb-4 flex gap-3">
      <button className={secondary} disabled={orders.loading} onClick={orders.newest}>Refresh newest orders</button>
      {orders.data?.next_cursor && <button className={secondary} disabled={orders.loading} onClick={orders.older}>Older orders</button>}
    </div>
    {orders.error && <Notice error>{orders.error}</Notice>}
    {orders.loading && <p>Loading local orders...</p>}
    {orders.data?.items.length === 0 && <p>No local orders match these filters. This does not mean your marketplace has no orders.</p>}
    <ul className="space-y-3">{orders.data?.items.map((order) => <li key={order.id} className="rounded-md border border-ink-200 p-3 text-sm">
      <h3 className="break-all font-semibold">{order.id}</h3>
      <p>{order.status} · Payment: {order.payment_status} · {order.currency} {order.total}</p>
      <p>Source: {order.channel} · Store: {order.external_shop_id ?? "Not recorded"}</p>
      <p>Platform order: {order.external_order_id ?? "Not recorded"}</p>
      <p>Tracking: {order.tracking_no ?? "Not recorded"} · Carrier: {order.carrier ?? "Not recorded"}</p>
      <p>Local update: {new Date(order.updated_at).toLocaleString()} — not a marketplace sync timestamp</p>
    </li>)}</ul>
  </>;
}
