"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { api } from "@/lib/api";
import {
  appendDemoListing,
  demoListings,
  listingsPath,
  orderCountsPath,
  orderListPath,
  type DemoListing,
  type Listing,
} from "@/lib/business";
import {
  activeFilters, businessPageContext, CATALOGUE_ABOUT_TEXT, channelLabel, chipGroupLabel,
  effectiveCountsRange, isListingOut, listingsEmptyText, orderChipOptions, orderStatusLabel,
  ORDERS_NOT_YET_TEXT, ordersAboutText, orderTiles, outOfStock, trackOpenOrder,
} from "@/lib/business-view";
import { todayLocal, toInstants, type DayRange } from "@/lib/date-range";
import type { Client, Me } from "@/lib/identity-contracts";
import { businessPages, type BusinessSection } from "@/lib/navigation";
import { usePublishContext } from "@/lib/page-context";
import { Fold, OrderCard, ProductCard, type Order, type Product } from "./business/cards";
import { ListingCard } from "./business/ListingCard";
import OrderFilters from "./business/OrderFilters";
import StoreConnections from "./business/StoreConnections";
import { useOrderCounts, useRecords } from "./business/useRecords";
import {
  DetailsRow, SectionNav, StatusChips, SummaryTiles, TechDetailsProvider, TechSwitch,
  useSectionArrivalFocus,
} from "./kit";
import { action, field, Heading, Notice, Panel, secondary } from "./ui";

export function BusinessWorkspace({ me, section = "orders" }: { me: Me; section?: BusinessSection }) {
  // The order cards that are open, oldest first; the newest one is the record the page publishes.
  const [openOrders, setOpenOrders] = useState<string[]>([]);
  const orderOpened = useCallback(
    (id: string, open: boolean) => setOpenOrders((current) => trackOpenOrder(current, id, open)),
    [],
  );
  // The same for TikTok listing cards on Products & stock: published as a product record.
  const [openListings, setOpenListings] = useState<string[]>([]);
  const listingOpened = useCallback(
    (id: string, open: boolean) => setOpenListings((current) => trackOpenOrder(current, id, open)),
    [],
  );
  usePublishContext(businessPageContext(section, openOrders, openListings));
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
        : section === "stores"
          ? "Your online stores and whether fourstacks is syncing them."
          : "Your orders, deliveries, products and stock, including TikTok Shop. View only."}
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
          {tenant
            ? <BusinessRecords key={tenant} query={tenantQuery} productsHref={pages[1].href} onOrderOpen={orderOpened} />
            : <>{needsTenant}<div className="mt-5"><OrdersFolds sync={null} /></div></>}
          <div className="mt-5">
            <button className={secondary} type="button" aria-pressed={demoMode} onClick={() => setDemoMode(true)}>
              See an example
            </button>
          </div>
        </>)}
    {section === "products" && (tenant
      ? <ProductRecords key={tenant} query={tenantQuery} onListingOpen={listingOpened} />
      : needsTenant)}
    {section === "stores" && (tenant ? <StoreConnections key={tenantQuery} me={me} query={tenantQuery} /> : needsTenant)}
  </TechDetailsProvider>;
}

/** The newest 50 products: the Out of stock tile counts them and the products page lists them. */
function productsPath(query: string): string {
  return `/console/v1/business/products?${query ? `${query}&` : ""}limit=50`;
}

/** The two long notices under the orders, worded for what is true now. `sync` is the order read's marketplace_sync. */
function OrdersFolds({ sync }: { sync: string | null }) {
  return <div className="space-y-3">
    <Fold testId="business-about" title="About this page">
      <Notice>{ordersAboutText(sync)}</Notice>
    </Fold>
    <Fold testId="business-not-yet" title="What this page does not do yet">
      <p>{ORDERS_NOT_YET_TEXT}</p>
    </Fold>
  </div>;
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

function BusinessRecords({ query, productsHref, onOrderOpen }: {
  query: string; productsHref: string; onOrderOpen: (id: string, open: boolean) => void;
}) {
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
      <OrderRecords orders={orders} onRefresh={refreshOrders} query={query} now={today} onOrderOpen={onOrderOpen} />
    </section>
    <OrdersFolds sync={orders.marketplaceSync} />
  </div>;
}

type ProductSource = "catalogue" | "tiktok";

function ProductRecords({ query, onListingOpen }: {
  query: string; onListingOpen: (id: string, open: boolean) => void;
}) {
  const products = useRecords<Product>(productsPath(query));
  // The Out of stock tile on the Orders page links here with ?stock=out.
  const search = useSearchParams();
  const [stock, setStock] = useState(() => (search.get("stock") === "out" ? "out" : "all"));
  const [source, setSource] = useState<ProductSource>("catalogue");
  const shown = stock === "out" ? products.items?.filter((product) => product.available_qty <= 0) : products.items;
  return <div className="mt-5">
    <Panel title="Products and stock">
      <div className="mb-4">
        <Fold testId="business-about-catalogue" title="About the catalogue">
          <p className="text-sm">{CATALOGUE_ABOUT_TEXT}</p>
        </Fold>
      </div>
      <div className="mb-4">
        <StatusChips mode="single" testId="business-source-switch" ariaLabel="Where the products come from"
          value={source}
          options={[{ value: "catalogue", label: "Your catalogue" }, { value: "tiktok", label: "TikTok Shop" }]}
          onChange={(next) => setSource(next as ProductSource)} />
      </div>
      {source === "tiktok"
        ? <ListingRecords query={query} stock={stock} onStock={setStock} sync={products.marketplaceSync}
            onListingOpen={onListingOpen} />
        : <>
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
            {products.items?.length === 0 && !products.loading && <p>No local products found. Products copied from TikTok Shop are under the TikTok Shop switch.</p>}
            {stock === "out" && products.items && products.items.length > 0 && shown?.length === 0 && <p>
              None of the products loaded so far are out of stock.</p>}
            <ul data-testid="business-products-list" className="space-y-3">
              {shown?.map((product) => <ProductCard key={product.id} product={product} />)}
            </ul>
          </>}
    </Panel>
  </div>;
}

/** The TikTok Shop products the sync copied: same Refresh and Older paging as the catalogue. */
function ListingRecords({ query, stock, onStock, sync, onListingOpen }: {
  query: string; stock: string; onStock: (next: string) => void; sync: string | null;
  onListingOpen: (id: string, open: boolean) => void;
}) {
  const listings = useRecords<Listing>(listingsPath(query));
  const [now, setNow] = useState(() => new Date());
  const shown = stock === "out" ? listings.items?.filter(isListingOut) : listings.items;
  return <>
    <div className="mb-4 flex flex-wrap items-center gap-3">
      <button className={secondary} disabled={listings.loading} data-testid="business-listings-refresh"
        onClick={() => { setNow(new Date()); listings.newest(); }}>Refresh newest products</button>
      {listings.next && <button className={secondary} disabled={listings.loading} data-testid="business-listings-older"
        onClick={listings.older}>Older products</button>}
      <TechSwitch testId="business-tech-all" />
    </div>
    <div className="mb-4">
      <StatusChips mode="single" testId="business-stock-filter" ariaLabel="Stock filter" value={stock}
        options={[{ value: "all", label: "All products" }, { value: "out", label: "Out of stock" }]}
        onChange={(next) => onStock(next as string)} />
      {stock === "out" && <p data-testid="business-stock-note" className="mt-2 text-sm text-[color:var(--fs-text-secondary)]">
        Out of stock among the products loaded so far.</p>}
    </div>
    {listings.error && <Notice error>{listings.error}</Notice>}
    {listings.loading && <p>Loading TikTok Shop products...</p>}
    {listings.items?.length === 0 && !listings.loading && <p data-testid="business-listings-empty">
      {listingsEmptyText(sync)}</p>}
    {stock === "out" && listings.items && listings.items.length > 0 && shown?.length === 0 && <p>
      None of the products loaded so far are out of stock.</p>}
    <ul data-testid="business-listings-list" className="space-y-3">
      {shown?.map((listing) => <ListingCard key={listing.id} listing={listing} now={now}
        onOpen={onListingOpen} />)}
    </ul>
  </>;
}

type OrdersState = ReturnType<typeof useRecords<Order>>;

function OrderRecords({ orders, onRefresh, query, now, onOrderOpen }: {
  orders: OrdersState; onRefresh: () => void; query: string; now: Date;
  onOrderOpen: (id: string, open: boolean) => void;
}) {
  return <>
    {orders.error && <Notice error>{orders.error}</Notice>}
    {orders.loading && <p>Loading local orders...</p>}
    {orders.items?.length === 0 && !orders.loading && <p>No local orders match these filters. This does not mean your marketplace has no orders.</p>}
    <ul data-testid="business-orders-list" className="space-y-3">
      {orders.items?.map((order) => <OrderCard key={order.id} order={order} query={query} now={now}
        onOpen={onOrderOpen} />)}
    </ul>
    <div className="mt-4 flex flex-wrap gap-3">
      <button className={secondary} disabled={orders.loading} data-testid="business-orders-refresh"
        onClick={onRefresh}>Refresh newest orders</button>
      {orders.next && <button className={secondary} disabled={orders.loading} onClick={orders.older}>
        {orders.loadingMore ? "Loading..." : "Older orders"}</button>}
    </div>
  </>;
}
