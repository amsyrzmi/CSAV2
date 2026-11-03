"use client";

import { useEffect, useRef, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
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
  activeFilters, channelLabel, chipGroupLabel, effectiveCountsRange, orderChipOptions, orderStatusLabel,
  orderTiles, outOfStock, whenText,
} from "@/lib/business-view";
import { todayLocal, toInstants, type DayRange } from "@/lib/date-range";
import type { Client, Me } from "@/lib/identity-contracts";
import { businessPages, type BusinessSection } from "@/lib/navigation";
import { usePublishContext } from "@/lib/page-context";
import { Fold, OrderCard, ProductCard, StoreCard, type Order, type Product } from "./business/cards";
import OrderFilters from "./business/OrderFilters";
import { useOrderCounts, useRecords } from "./business/useRecords";
import {
  DetailsRow, SectionNav, StatusChips, SummaryTiles, TechDetailsProvider, TechnicalDetails, TechSwitch,
  useSectionArrivalFocus,
} from "./kit";
import { action, field, Heading, Notice, Panel, secondary } from "./ui";

type Store = {
  id: string; platform: string; shop_id: string | null; expires_at: string | null;
  updated_at: string; expiry: "unknown" | "expired" | "not_expired";
  authorization: "unverified"; permissions: "unknown"; provider_reads: "unavailable";
  connection: { status: ConnectionStatus; live_integration: "unavailable" };
};

export function BusinessWorkspace({ me, section = "orders" }: { me: Me; section?: BusinessSection }) {
  usePublishContext({ tab: "business", section });
  const search = useSearchParams();
  const pathname = usePathname();
  const router = useRouter();
  const platform = me.role === "platform_admin";
  const permitted = platform || me.role === "tenant_admin" || me.role === "tenant_staff";
  const [clients, setClients] = useState<Client[]>([]);
  // An administrator's chosen tenant lives in the URL (?client_id=) and nowhere else, so the
  // screen always agrees with the address: a section-bar click, a reload, Back and a rail link
  // without a tenant all show what the URL says.
  const selected = search.get("client_id") ?? "";
  const [error, setError] = useState("");
  const [demoMode, setDemoMode] = useState(false);
  const pageHeading = useRef<HTMLHeadingElement>(null);
  useSectionArrivalFocus(pageHeading);
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
  const tenantQuery = platform && tenant ? `client_id=${encodeURIComponent(tenant)}` : "";
  const pages = businessPages(platform);
  const pageTitle = pages.find((page) => page.key === section)?.label ?? "Orders";
  const chooseTenant = (value: string) => {
    router.replace(value ? `${pathname}?client_id=${encodeURIComponent(value)}` : pathname, { scroll: false });
  };
  const needsTenant = <Notice>Select a tenant to view its business records.</Notice>;
  return <TechDetailsProvider>
    <Heading
      title="Business management"
      description={demoMode
        ? "Synthetic demo only — sample listings do not connect to a seller account."
        : "Your orders, deliveries, products and stock saved in fourstacks. View only."}
    />
    <SectionNav label="Business management pages" testId="business-subnav" items={pages}
      current={section} query={tenantQuery || undefined} />
    <h2 ref={pageHeading} tabIndex={-1} data-testid="business-page-heading"
      className="mb-2 font-(family-name:--fs-font-heading) text-(length:--fs-text-xl) font-semibold focus:outline-none sm:mb-3">
      {pageTitle}
    </h2>
    {section === "orders" && demoMode && <div className="mb-5">
      <button className={secondary} type="button" aria-pressed={demoMode} onClick={() => setDemoMode(false)}>
        Hide the example
      </button>
    </div>}
    {section === "orders" && demoMode && <Notice>Example data only, made up for this view. Not connected to TikTok; nothing here is sent
          to TikTok. Changes stay on this page for now and disappear when you leave or reload.</Notice>}
    {!(section === "orders" && demoMode) && error && <Notice error>{error}</Notice>}
    {!(section === "orders" && demoMode) && platform && <label className="my-5 block">Tenant
      <select className={field} value={selected} onChange={(event) => chooseTenant(event.target.value)}>
        <option value="">Select a tenant</option>
        {clients.map((client) => <option key={client.id} value={client.id}>{client.name}</option>)}
      </select>
    </label>}
    {section === "orders" && (demoMode
      ? <DemoBusinessWorkspace />
      : <>
          {tenant ? <BusinessRecords key={tenant} query={tenantQuery} productsHref={pages[1].href} /> : needsTenant}
          <div className="mt-5 space-y-3">
            <Fold testId="business-about" title="About this page">
              <Notice>These are orders and products saved in fourstacks. They are not a live TikTok Shop
                feed and don&apos;t update from TikTok Shop or Shopee yet. Stores are not connected or
                checked, and stock is not matched or checked across stores. Use Refresh to see the newest saved
                records.</Notice>
            </Fold>
            <Fold testId="business-not-yet" title="What this page does not do yet">
              <h3 className="mb-2 font-semibold">Not available yet</h3>
              <p>Coming later: marketplace listings, connecting or changing stores, live courier tracking,
                listing edits, stock updates, fulfilment, cancellations, returns and refunds, payments and
                syncing between platforms. Support cases, invoices, PDF downloads and staff preparation are
                coming later too. No seller account is connected or checked yet.</p>
            </Fold>
          </div>
          <div className="mt-5">
            <button className={secondary} type="button" aria-pressed={demoMode} onClick={() => setDemoMode(true)}>
              See an example
            </button>
          </div>
        </>)}
    {section === "products" && (tenant ? <ProductRecords key={tenant} query={tenantQuery} /> : needsTenant)}
    {section === "stores" && (tenant ? <StoreRecords key={tenantQuery} query={tenantQuery} /> : needsTenant)}
  </TechDetailsProvider>;
}

/** The newest 50 products: the Out of stock tile counts them and the products page lists them. */
function productsPath(query: string): string {
  return `/console/v1/business/products?${query ? `${query}&` : ""}limit=50`;
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
          <p>RM {listing.price} · {listing.status} · Demo only, not published</p>
        </li>)}
      </ul>
    </Panel>
    <Panel title="Demo connection status">
      <p className="text-sm">No seller account is connected. Partner eligibility, shop authorization and
        live listing permissions have not been verified in this demo.</p>
    </Panel>
  </div>;
}

function BusinessRecords({ query, productsHref }: { query: string; productsHref: string }) {
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
  // The out-of-stock tile counts the newest 50 products: one read, no paging.
  const stock = useRecords<Product>(productsPath(query));
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
  const countsLoading = counts === null && !failed;
  const stockRead = stock.items ? outOfStock(stock.items, stock.next !== null) : null;
  const tiles = [
    ...orderTiles(counts, status).map((tile) => ({
      key: tile.key,
      label: tile.label,
      value: tile.value,
      loading: countsLoading,
      pressed: tile.pressed,
      onPress: () => setStatus(tile.pressed ? "all" : tile.status),
      testId: `business-tile-${tile.key.replaceAll("_", "-")}`,
    })),
    {
      key: "out_of_stock",
      label: "Out of stock",
      value: stockRead ? stockRead.value : null,
      loading: stock.items === null && !stock.error,
      caption: stockRead?.caption ?? undefined,
      href: `${productsHref}?stock=out${query ? `&${query}` : ""}`,
      testId: "business-tile-out-of-stock",
    },
  ];
  const filters = activeFilters({ range, payment, status });
  const removeFilter = (key: "date" | "payment" | "status") => {
    if (key === "date") { setRange({ from: null, to: null }); setAllTime(false); }
    else if (key === "payment") setPayment("");
    else setStatus("all");
  };
  return <div className="space-y-5">
    <section className="card rounded-[14px] p-3 sm:p-6">
      {/* The section tab above already says Orders: on a phone the heading stays for screen readers
          and gives its line back, so the first order is on the first screen. */}
      <h2 className="sr-only font-(family-name:--fs-font-heading) text-base font-semibold sm:not-sr-only sm:mb-5">Orders</h2>
      <SummaryTiles ariaLabel="Orders at a glance" testId="business-tiles" tiles={tiles} />
      <div data-testid="business-counts-note"
        className="mb-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-[color:var(--fs-text-secondary)]">
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
          data-testid="business-counts-all-time" onClick={() => setAllTime(true)}>Show totals for all dates</button>}
        {scope.kind === "all" && <button type="button" className={secondary}
          data-testid="business-counts-default" onClick={() => setAllTime(false)}>Use the last 30 days</button>}
        <button type="button" className={secondary} disabled={orders.loading}
          data-testid="business-orders-refresh-top" onClick={refreshOrders}>Refresh newest orders</button>
      </div>
      <OrderFilters range={range} onRange={(next) => { setRange(next); setAllTime(false); }}
        payment={payment} onPayment={setPayment}>
        <Fold testId="business-more-filters" title="More filters"
          hint={applied.channel ? `Source: ${channelLabel(applied.channel)}` : undefined}>
          <form className="space-y-3" onSubmit={(event) => {
            event.preventDefault();
            setApplied({ channel, shopId });
          }}>
            <label className="block">Where the order came from
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
          <p className="mt-3 text-sm">Showing: {applied.channel ? channelLabel(applied.channel) : "all sources"}
            {", "}{applied.shopId ? `store ${applied.shopId}` : "all stores"}</p>
        </Fold>
      </OrderFilters>
      {(filters.length > 0 || applied.channel || applied.shopId) && <div data-testid="business-active-filters"
        className="mb-3 flex flex-wrap items-center gap-x-3 gap-y-2 text-sm">
        {filters.map((filter) => <span key={filter.key} data-testid={`business-active-${filter.key}`}
          className="inline-flex min-h-10 items-center gap-1 rounded-[var(--fs-radius-round)] border border-[color:var(--fs-border-control)] bg-[var(--fs-surface)] py-0.5 pr-1 pl-3">
          {filter.label}
          <button type="button" aria-label={`Remove ${filter.label} filter`}
            data-testid={`business-active-${filter.key}-remove`} onClick={() => removeFilter(filter.key)}
            className="inline-flex size-8 items-center justify-center rounded-[var(--fs-radius-round)] hover:bg-[var(--fs-surface-blue)] pointer-coarse:size-10">
            <svg aria-hidden="true" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor"
              strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><path d="M6 6l12 12M18 6 6 18" /></svg>
          </button>
        </span>)}
        {(applied.channel || applied.shopId) && <div data-testid="business-source-filter"
          className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <span>Showing orders from {channelLabel(applied.channel)} only{applied.shopId ? `, store ${applied.shopId}` : ""}</span>
          <button type="button" className={secondary} data-testid="business-source-clear" onClick={() => {
            setChannel(""); setShopId(""); setApplied({ channel: "", shopId: "" });
          }}>Show all sources</button>
        </div>}
      </div>}
      <div className="mb-3 rounded-[var(--fs-radius-panel)] border border-[color:var(--fs-border)] bg-[var(--fs-surface)] p-3">
        <DetailsRow testId="business-all-statuses" toggleLabel="all statuses"
          summary={<p className="font-medium">Showing: {status === "all" ? "All orders" : orderStatusLabel(status)}</p>}>
          <StatusChips mode="single" testId="business-status" ariaLabel={chipGroupLabel(scope.kind)}
            options={orderChipOptions(counts)} value={status}
            onChange={(next) => setStatus(next as string)} />
        </DetailsRow>
      </div>
      <OrderRecords orders={orders} onRefresh={refreshOrders} />
    </section>
  </div>;
}

function ProductRecords({ query }: { query: string }) {
  const products = useRecords<Product>(productsPath(query));
  // The Out of stock tile on the Orders page links here with ?stock=out.
  const search = useSearchParams();
  const [stock, setStock] = useState(() => (search.get("stock") === "out" ? "out" : "all"));
  const shown = stock === "out" ? products.items?.filter((product) => product.available_qty <= 0) : products.items;
  return <div className="mt-5">
    <Panel title="Products and stock">
      <div className="mb-4">
        <Fold testId="business-about-catalogue" title="About the catalogue">
          <p className="text-sm">Stock here is for your whole business, not per store. The order
            filters do not change these products, and products are not matched to store listings yet.</p>
        </Fold>
      </div>
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <button className={secondary} disabled={products.loading} onClick={products.newest}>Refresh newest products</button>
        {products.next && <button className={secondary} disabled={products.loading} onClick={products.older}>Older products</button>}
        <TechSwitch testId="business-tech-all" />
      </div>
      <div className="mb-4">
        <StatusChips mode="single" testId="business-stock-filter" ariaLabel="Stock filter" value={stock}
          options={[{ value: "all", label: "All products" }, { value: "out", label: "Out of stock" }]}
          onChange={(next) => setStock(next as string)} />
        {stock === "out" && <p data-testid="business-stock-note" className="mt-2 text-sm text-[color:var(--fs-text-secondary)]">
          Out of stock among the products loaded so far.</p>}
      </div>
      {products.error && <Notice error>{products.error}</Notice>}
      {products.loading && <p>Loading local products...</p>}
      {products.items?.length === 0 && !products.loading && <p>No local products found. Marketplace listings are not imported by this view.</p>}
      {stock === "out" && products.items && products.items.length > 0 && shown?.length === 0 && <p>
        None of the products loaded so far are out of stock.</p>}
      <ul data-testid="business-products-list" className="space-y-3">
        {shown?.map((product) => <ProductCard key={product.id} product={product} />)}
      </ul>
    </Panel>
  </div>;
}

function StoreRecords({ query }: { query: string }) {
  const [platform, setPlatform] = useState("");
  const params = new URLSearchParams(query);
  if (platform) params.set("platform", platform);
  const path = `/console/v1/business/stores?${params}`;
  return <div className="mt-5 space-y-5">
    <section className="card rounded-[14px] p-5 sm:p-6">
      <div className="mb-3 flex flex-wrap gap-3">
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
      <h3 className="mb-3 font-semibold">Online stores (saved records only; connecting is coming soon)</h3>
      <StorePage key={path} path={path} />
    </section>
    <Fold testId="business-stores" title="About store connections">
      <Notice>Store connections are coming soon. These are saved store details only, not a live
        check, and saved access or a future expiry does not prove a store is connected.</Notice>
      <TechnicalDetails testId="business-stores-tech">
        <Notice>These are local records and stored lifecycle evidence, not a live connection check.
          Token contents, shop ciphers and permission scope values are not returned. Current provider
          authorization and permissions remain unverified; provider reads are unavailable.
          Recorded authorization or a future expiry does not prove a working connection.</Notice>
        <p className="my-3 text-sm">Live TikTok authorization requires an eligible Partner Center app
          and seller authorization. Account registration alone does not establish eligibility;
          review/publishing or an independently verified testing route is still required.
          Live authorization is not enabled in this workspace.</p>
        <p className="text-sm">Pages follow stable local record IDs, not connection or update recency.</p>
      </TechnicalDetails>
    </Fold>
  </div>;
}

function StorePage({ path }: { path: string }) {
  const stores = useRecords<Store>(path);
  return <>
    <div className="mb-4 flex flex-wrap items-center gap-3">
      <button className={secondary} disabled={stores.loading} onClick={stores.newest}>Refresh local store records</button>
      {stores.next && <button className={secondary} disabled={stores.loading} onClick={stores.older}>Next store records</button>}
      <TechSwitch testId="business-tech-all" />
    </div>
    {stores.error && <Notice error>{stores.error}</Notice>}
    {stores.loading && <p>Loading local store records...</p>}
    {stores.items?.length === 0 && !stores.loading && <p>No matching local store records. This does not prove the marketplace has no authorized stores.</p>}
    <ul data-testid="business-stores-list" className="space-y-3">{stores.items?.map((store) => <StoreCard key={store.id} id={store.id} store={store}>
      <h3 className="break-all font-semibold">{store.platform} · {store.shop_id ?? "Store ID not recorded"}</h3>
      <p className="break-all">Local record: {store.id}</p>
      <p>Stored lifecycle: {connectionLabels[store.connection.status]}</p>
      <p>Live integration: {store.connection.live_integration}</p>
      <p>Authorization: {store.authorization} · Permissions: {store.permissions} · Provider reads: {store.provider_reads}</p>
      <p>Recorded expiry: {store.expires_at ? whenText(store.expires_at) : "Unknown"}
        {" · "}{store.expiry === "not_expired" ? "Not expired by recorded timestamp only" : store.expiry === "expired" ? "Expired by recorded timestamp" : "Expiry unknown"}</p>
      <p>Local update: {whenText(store.updated_at)} — not a provider verification timestamp</p>
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
