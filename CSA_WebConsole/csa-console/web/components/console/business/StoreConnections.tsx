"use client";

import { useEffect, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { api, ApiError } from "@/lib/api";
import { connectionLabels, type ConnectionStatus } from "@/lib/business";
import { channelLabel, offersConnect, storeActions, storeSummary, whenText } from "@/lib/business-view";
import type { Me } from "@/lib/identity-contracts";
import {
  connectErrorText, connectResult, navigate, syncLine, TIKTOK_UNAVAILABLE, type ConnectResult,
} from "@/lib/tiktok-connect";
import { TechnicalDetails, TechSwitch } from "../kit";
import { action, field, Notice, secondary } from "../ui";
import { Fold, StoreCard } from "./cards";
import { useRecords } from "./useRecords";

type Store = {
  id: string; platform: string; shop_id: string | null; expires_at: string | null;
  updated_at: string; expiry: "unknown" | "expired" | "not_expired";
  authorization: "verified" | "unverified";
  permissions: "granted" | "missing" | "unknown";
  provider_reads: "available" | "unavailable";
  connection: { status: ConnectionStatus; live_integration: "available" | "unavailable" };
  sync: {
    orders_synced_at: string | null; products_synced_at: string | null; problem: "sync_failing" | null;
  } | null;
  can_manage: boolean;
};

const DISCONNECT_CONFIRM = "Stop syncing this shop? Orders stay visible for 30 days, then customer details are removed.";
const CONNECT_PATH = "/console/v1/business/stores/tiktok/connect";

/** The Store connections page: connect, reconnect and disconnect a TikTok shop, and each shop's status. */
export default function StoreConnections({ me, query }: { me: Me; query: string }) {
  const search = useSearchParams();
  const pathname = usePathname();
  const router = useRouter();
  const [platform, setPlatform] = useState("");
  const params = new URLSearchParams(query);
  if (platform) params.set("platform", platform);
  const stores = useRecords<Store>(`/console/v1/business/stores?${params}`);
  const [now, setNow] = useState(() => new Date());
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState("");
  const [unavailable, setUnavailable] = useState(false);
  const canManage = me.role === "platform_admin" || me.role === "tenant_admin";

  // Back from TikTok: show the one-line result once, then drop `tiktok` and `reason` from the
  // address (a platform admin keeps `client_id`). The state is set while rendering, as
  // useRecords does for a new path, so the line shows on the first paint after the return.
  const arrival = search.has("tiktok") ? `${search.get("tiktok")}:${search.get("reason") ?? ""}` : "";
  const [seenArrival, setSeenArrival] = useState("");
  const [result, setResult] = useState<ConnectResult | null>(null);
  if (arrival !== seenArrival && (arrival || seenArrival)) {
    setSeenArrival(arrival);
    if (arrival) {
      setResult(connectResult(search));
      if (search.get("reason") === "tiktok_unavailable") setUnavailable(true);
    }
  }
  useEffect(() => {
    if (!arrival) return;
    const rest = new URLSearchParams(search.toString());
    rest.delete("tiktok");
    rest.delete("reason");
    const text = rest.toString();
    router.replace(text ? `${pathname}?${text}` : pathname, { scroll: false });
  }, [arrival, search, pathname, router]);

  const withTenant = (path: string) => (query ? `${path}?${query}` : path);

  // Connect (no store) and Reconnect (that store) both end by leaving for TikTok's own page.
  async function begin(storeId?: string) {
    setBusy(true);
    setProblem("");
    try {
      const started = await api<{ authorization_url: string }>(withTenant(CONNECT_PATH), {
        method: "POST",
        body: JSON.stringify(storeId ? { store_id: storeId } : {}),
      });
      if (navigate(started.authorization_url)) return; // The browser is leaving; the buttons stay off.
      setProblem("We couldn't start the connection. Try again in a moment.");
    } catch (cause) {
      const failure = cause instanceof ApiError ? cause : null;
      if (failure?.status === 503 && failure.code === "tiktok_unavailable") setUnavailable(true);
      setProblem(connectErrorText(failure?.code ?? "", cause instanceof Error ? cause.message : "Something went wrong."));
    }
    setBusy(false);
  }

  async function disconnect(storeId: string) {
    if (!window.confirm(DISCONNECT_CONFIRM)) return;
    setBusy(true);
    setProblem("");
    try {
      await api(withTenant(`/console/v1/business/stores/${encodeURIComponent(storeId)}/disconnect`), { method: "POST" });
      setNow(new Date());
      stores.newest();
    } catch (cause) {
      const failure = cause instanceof ApiError ? cause : null;
      setProblem(connectErrorText(failure?.code ?? "", cause instanceof Error ? cause.message : "Something went wrong."));
    }
    setBusy(false);
  }

  const items = stores.items ?? [];
  const showConnect = canManage && stores.items !== null && !stores.loading && stores.next === null
    && (platform === "" || platform === "tiktok_shop") && offersConnect(items);
  const off = busy || unavailable;
  return <div className="mt-5 space-y-5">
    {result && <Notice error={result.tone === "bad"} testId="business-tiktok-result">{result.text}</Notice>}
    <section className="card rounded-[14px] p-5 sm:p-6">
      {showConnect && <div className="mb-3 flex flex-wrap gap-3">
        <button className={action} type="button" disabled={off} data-testid="business-store-connect"
          onClick={() => void begin()}>Connect TikTok Shop</button>
      </div>}
      {unavailable && result?.text !== TIKTOK_UNAVAILABLE && <div className="mb-3">
        <Notice testId="business-store-unavailable">{TIKTOK_UNAVAILABLE}</Notice>
      </div>}
      {problem && <div className="mb-3"><Notice error testId="business-store-problem">{problem}</Notice></div>}
      {!canManage && <p data-testid="business-store-staff-note" className="mb-3 text-sm text-[color:var(--fs-text-secondary)]">
        Only an Admin can connect or disconnect a shop. You can see its status here.</p>}
      <label className="my-3 block">Marketplace
        <select className={field} value={platform} onChange={(event) => setPlatform(event.target.value)}>
          <option value="">All marketplaces</option>
          <option value="tiktok_shop">TikTok Shop</option>
          <option value="shopee">Shopee</option>
          <option value="lazada">Lazada</option>
        </select>
      </label>
      <h3 className="mb-3 font-semibold">Online stores</h3>
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <button className={secondary} disabled={stores.loading} onClick={() => { setNow(new Date()); stores.newest(); }}>
          Refresh stores</button>
        {stores.next && <button className={secondary} disabled={stores.loading} onClick={stores.older}>More stores</button>}
        <TechSwitch testId="business-tech-all" />
      </div>
      {stores.error && <Notice error>{stores.error}</Notice>}
      {stores.loading && <p>Loading stores...</p>}
      {stores.items?.length === 0 && !stores.loading && <p>{canManage
        ? "No online store is connected yet. Connect TikTok Shop to start syncing its orders and products."
        : "No online store is connected yet. An Admin can connect one."}</p>}
      <ul data-testid="business-stores-list" className="space-y-3">
        {items.map((store) => {
          const buttons = storeActions(store);
          const title = storeSummary(store).title;
          const pending = store.connection.status === "missing_shop_id";
          return <StoreCard key={store.id} id={store.id} store={store} extra={<>
            {store.sync && !pending && <div data-testid={`business-store-${store.id}-sync`}
              className="mt-1.5 text-sm text-[color:var(--fs-text-secondary)]">
              <p>{syncLine("Orders", store.sync.orders_synced_at, now)}</p>
              <p>{syncLine("Products", store.sync.products_synced_at, now)}</p>
            </div>}
            {store.sync?.problem === "sync_failing" && <p data-testid={`business-store-${store.id}-problem`}
              className="mt-1.5 text-sm font-medium text-[color:var(--fs-warning)]">
              Syncing is failing. We&apos;ll keep trying, and tell you if it keeps failing.</p>}
            {(buttons.reconnect || buttons.disconnect) && <div className="mt-2 flex flex-wrap gap-2">
              {buttons.reconnect && <button className={secondary} type="button" disabled={off}
                aria-label={`Reconnect ${title}`} data-testid={`business-store-reconnect-${store.id}`}
                onClick={() => void begin(store.id)}>Reconnect</button>}
              {buttons.disconnect && <button className={secondary} type="button" disabled={busy}
                aria-label={`Disconnect ${title}`} data-testid={`business-store-disconnect-${store.id}`}
                onClick={() => void disconnect(store.id)}>Disconnect</button>}
            </div>}
          </>}>
            <h3 className="break-all font-semibold">{channelLabel(store.platform)} · {store.shop_id ?? "Store ID not recorded"}</h3>
            <p className="break-all">Record: {store.id}</p>
            <p>Stored status: {connectionLabels[store.connection.status] ?? store.connection.status}</p>
            <p>Live integration: {store.connection.live_integration}</p>
            <p>Authorization: {store.authorization} · Permissions: {store.permissions} · Provider reads: {store.provider_reads}</p>
            <p>Access expires: {store.expires_at ? whenText(store.expires_at) : "Unknown"}
              {" · "}{store.expiry === "not_expired" ? "Not expired" : store.expiry === "expired" ? "Expired" : "Expiry unknown"}</p>
            <p>Last changed here: {whenText(store.updated_at)}</p>
          </StoreCard>;
        })}
      </ul>
    </section>
    <Fold testId="business-stores" title="About store connections">
      <Notice>Connecting a shop lets fourstacks read its orders and products from TikTok Shop. fourstacks
        doesn&apos;t change anything in your shop. Only an Admin can connect, reconnect or disconnect a shop;
        Staff can see its status. After you disconnect, orders stay visible for 30 days, then customer
        details are removed.</Notice>
      <TechnicalDetails testId="business-stores-tech">
        <Notice>Access tokens, shop ciphers and permission values are stored encrypted and are never shown
          here. The status comes from the saved authorization, its permissions and its expiry. Orders and
          products are read from TikTok on a schedule. If TikTok rejects a renewal of the access, the shop
          shows Needs reconnecting.</Notice>
        <p className="my-3 text-sm">Connecting needs the TikTok Shop app for this server to be set up. If it
          isn&apos;t, the Connect button says so and stays off.</p>
        <p className="text-sm">Pages follow stable record IDs, not connection or update recency.</p>
      </TechnicalDetails>
    </Fold>
  </div>;
}
