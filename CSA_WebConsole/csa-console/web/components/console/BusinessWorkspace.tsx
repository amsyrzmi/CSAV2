"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import {
  appendDemoListing,
  connectionLabels,
  demoListings,
  orderCountsPath,
  orderListPath,
  type ConnectionStatus,
  type DemoListing,
} from "@/lib/business";
import {
  channelLabel, chipGroupLabel, effectiveCountsRange, orderChipOptions, paymentOptions,
} from "@/lib/business-view";
import { todayLocal, toInstants, type DayRange } from "@/lib/date-range";
import type { Client, Me } from "@/lib/identity-contracts";
import { usePublishContext } from "@/lib/page-context";
import { Fold, OrderCard, ProductCard, StoreCard, type Order, type Product } from "./business/cards";
import { useOrderCounts, useRecords } from "./business/useRecords";
import { DateRangeFilter, StatusChips } from "./kit";
import { action, field, Heading, Notice, Panel, secondary } from "./ui";

type Store = {
  id: string; platform: string; shop_id: string | null; expires_at: string | null;
  updated_at: string; expiry: "unknown" | "expired" | "not_expired";
  authorization: "unverified"; permissions: "unknown"; provider_reads: "unavailable";
  connection: { status: ConnectionStatus; live_integration: "unavailable" };
};

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
    {demoMode && <Notice>DEMO MODE — fictional sample data only. Not connected to TikTok; no TikTok API request or
          listing is sent. Demo changes stay in this page&apos;s temporary state and reset when you exit
          or reload.</Notice>}
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
          <div className="mt-5 space-y-3">
            <Fold testId="business-about" title="About this page">
              <Notice>Local records only, not a live TikTok Shop feed. No marketplace synchronization or store
                connection verification is performed. Stock is not synchronized or authoritative across stores.
                Refresh explicitly to read current local records.</Notice>
            </Fold>
            <Fold testId="business-not-yet" title="What this page does not do yet">
              <h3 className="mb-2 font-semibold">Not available in this slice</h3>
              <p>Marketplace listings, connection verification or changes, live carrier tracking, listing edits,
                stock updates, fulfilment, cancellations, returns/refunds, payments and cross-platform synchronization
                are unavailable here. Tickets, invoices, PDF downloads and staff preparation are later M6 slices.
                Existing adapter code does not establish live seller authorization or a verified connection.</p>
            </Fold>
          </div>
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
  const [applied, setApplied] = useState({ channel: "", shopId: "" });
  const [range, setRange] = useState<DayRange>({ from: null, to: null });
  const [allTime, setAllTime] = useState(false);
  const [status, setStatus] = useState("all");
  const [payment, setPayment] = useState("");
  // "Today" for the default counts window; read again on every explicit refresh.
  const [today, setToday] = useState(() => new Date());
  const [refresh, setRefresh] = useState(0);
  const products = useRecords<Product>(`/console/v1/business/products?${query}`);
  // The default window follows the calendar: when the tab comes back on a new day, move it on.
  useEffect(() => {
    const onShow = () => {
      if (document.visibilityState !== "visible") return;
      setToday((current) => (todayLocal(current) === todayLocal() ? current : new Date()));
    };
    document.addEventListener("visibilitychange", onShow);
    return () => document.removeEventListener("visibilitychange", onShow);
  }, []);
  const created = toInstants(range);
  const scope = effectiveCountsRange(range, allTime, today);
  const counted = toInstants(scope.range);
  const narrowed = { ...applied, paymentStatus: payment };
  const listPath = orderListPath(query, {
    ...narrowed, ...created, status: status === "all" ? "" : status,
  });
  const orders = useRecords<Order>(listPath);
  const refreshOrders = () => { orders.newest(); setToday(new Date()); setRefresh((value) => value + 1); };
  const { counts, failed } = useOrderCounts(orderCountsPath(query, { ...narrowed, ...counted }), refresh);
  return <div className="mt-5 space-y-5">
    <section className="card rounded-[14px] p-3 sm:p-6">
      <h2 className="mb-3 font-(family-name:--fs-font-heading) text-base font-semibold sm:mb-5">Orders and tracking references</h2>
      <div className="mb-3 flex flex-wrap items-end gap-3">
        <div className="shrink-0">
          <p aria-hidden="true" className="mb-1 text-sm">Order date</p>
          <DateRangeFilter testId="business-date" label="Order date" value={range}
            onChange={(next) => { setRange(next); setAllTime(false); }} />
        </div>
        <div className="min-w-0 flex-1 basis-40 sm:w-64 sm:flex-none">
          <label htmlFor="business-payment" className="mb-1 block text-sm">Payment status</label>
          <select id="business-payment" data-testid="business-payment" className={field} value={payment}
            onChange={(event) => setPayment(event.target.value)}>
            <option value="">Any payment status</option>
            {paymentOptions().map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
          </select>
        </div>
      </div>
      {/* One scrolling row on phones (a part of the next chip shows as the hint), wrapping from 640 px. */}
      <div className="min-w-0 max-sm:[&>[role=group]]:flex-nowrap max-sm:[&>[role=group]]:overflow-x-auto max-sm:[&>[role=group]]:overscroll-x-contain max-sm:[&>[role=group]]:py-1 max-sm:[&>[role=group]]:[scroll-snap-type:x_mandatory] max-sm:[&>[role=group]>button]:shrink-0 max-sm:[&>[role=group]>button]:[scroll-snap-align:start]">
        <StatusChips mode="single" testId="business-status" ariaLabel={chipGroupLabel(scope.kind)}
          options={orderChipOptions(counts)} value={status}
          onChange={(next) => setStatus(next as string)} />
      </div>
      <div data-testid="business-counts-note"
        className="my-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-[color:var(--fs-text-secondary)]">
        {failed
          ? <span data-testid="business-counts-failed" role="status">The order numbers could not be
              loaded just now. The list below still works.</span>
          : <>
              <span>{scope.kind === "chosen" ? "The numbers cover the dates you chose."
                : scope.kind === "all" ? "The numbers cover all time. This can be slow for a busy shop."
                : "The numbers cover the last 30 days."}</span>
              {scope.kind !== "chosen" && <span>The list shows every date until you choose dates.</span>}
            </>}
        {scope.kind === "default" && <button type="button" className={secondary}
          data-testid="business-counts-all-time" onClick={() => setAllTime(true)}>Count all time</button>}
        {scope.kind === "all" && <button type="button" className={secondary}
          data-testid="business-counts-default" onClick={() => setAllTime(false)}>Use the last 30 days</button>}
        <button type="button" className={secondary} disabled={orders.loading}
          data-testid="business-orders-refresh-top" onClick={refreshOrders}>Refresh newest orders</button>
      </div>
      {(applied.channel || applied.shopId) && <div data-testid="business-source-filter"
        className="mb-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
        <span>Showing orders from {channelLabel(applied.channel)} only{applied.shopId ? `, store ${applied.shopId}` : ""}</span>
        <button type="button" className={secondary} data-testid="business-source-clear" onClick={() => {
          setChannel(""); setShopId(""); setApplied({ channel: "", shopId: "" });
        }}>Show all sources</button>
      </div>}
      <OrderRecords orders={orders} onRefresh={refreshOrders} />
      <div className="mt-4">
        <Fold testId="business-more-filters" title="More filters"
          hint={applied.channel ? `Source: ${channelLabel(applied.channel)}` : undefined}>
          <form className="space-y-3" onSubmit={(event) => {
            event.preventDefault();
            setApplied({ channel, shopId });
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
          <p className="mt-3 text-sm">Applied source: {applied.channel || "All local sources"}
            {" · "}Applied store ID: {applied.shopId || "All stores / not recorded"}</p>
        </Fold>
      </div>
    </section>
    <Panel title="Local catalogue and inventory">
      <div className="mb-4">
        <Fold testId="business-about-catalogue" title="About the catalogue">
          <p className="text-sm">Tenant-wide local stock. Order source/store filters do not filter
            these products; verified store listing mappings are not available in this slice.</p>
        </Fold>
      </div>
      <div className="mb-4 flex gap-3">
        <button className={secondary} disabled={products.loading} onClick={products.newest}>Refresh newest products</button>
        {products.next && <button className={secondary} disabled={products.loading} onClick={products.older}>Older products</button>}
      </div>
      {products.error && <Notice error>{products.error}</Notice>}
      {products.loading && <p>Loading local products...</p>}
      {products.items?.length === 0 && !products.loading && <p>No local products found. Marketplace listings are not imported by this view.</p>}
      <ul data-testid="business-products-list" className="space-y-3">
        {products.items?.map((product) => <ProductCard key={product.id} product={product} />)}
      </ul>
    </Panel>
    <Fold testId="business-stores" title="Store connections and local store records">
      <h3 className="mb-3 font-semibold">Local store records (not verified connections)</h3>
      <StoreRecords key={query} query={query} />
    </Fold>
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
      {stores.next && <button className={secondary} disabled={stores.loading} onClick={stores.older}>Next store records</button>}
    </div>
    <p className="mb-3 text-sm">Pages follow stable local record IDs, not connection or update recency.</p>
    {stores.error && <Notice error>{stores.error}</Notice>}
    {stores.loading && <p>Loading local store records...</p>}
    {stores.items?.length === 0 && !stores.loading && <p>No matching local store records. This does not prove the marketplace has no authorized stores.</p>}
    <ul data-testid="business-stores-list" className="space-y-3">{stores.items?.map((store) => <StoreCard key={store.id} id={store.id} store={store}>
      <h3 className="break-all font-semibold">{store.platform} · {store.shop_id ?? "Store ID not recorded"}</h3>
      <p className="break-all">Local record: {store.id}</p>
      <p>Stored lifecycle: {connectionLabels[store.connection.status]}</p>
      <p>Live integration: {store.connection.live_integration}</p>
      <p>Authorization: {store.authorization} · Permissions: {store.permissions} · Provider reads: {store.provider_reads}</p>
      <p>Recorded expiry: {store.expires_at ? new Date(store.expires_at).toLocaleString() : "Unknown"}
        {" · "}{store.expiry === "not_expired" ? "Not expired by recorded timestamp only" : store.expiry === "expired" ? "Expired by recorded timestamp" : "Expiry unknown"}</p>
      <p>Local update: {new Date(store.updated_at).toLocaleString()} — not a provider verification timestamp</p>
    </StoreCard>)}</ul>
  </>;
}

type OrdersState = ReturnType<typeof useRecords<Order>>;

function OrderRecords({ orders, onRefresh }: { orders: OrdersState; onRefresh: () => void }) {
  return <>
    {orders.error && <Notice error>{orders.error}</Notice>}
    {orders.loading && <p>Loading local orders...</p>}
    {orders.items?.length === 0 && !orders.loading && <p>No local orders match these filters. This does not mean your marketplace has no orders.</p>}
    <ul data-testid="business-orders-list" className="space-y-3">
      {orders.items?.map((order) => <OrderCard key={order.id} order={order} />)}
    </ul>
    <div className="mt-4 flex flex-wrap gap-3">
      <button className={secondary} disabled={orders.loading} data-testid="business-orders-refresh"
        onClick={onRefresh}>Refresh newest orders</button>
      {orders.next && <button className={secondary} disabled={orders.loading} onClick={orders.older}>
        {orders.loadingMore ? "Loading..." : "Older orders"}</button>}
    </div>
  </>;
}
