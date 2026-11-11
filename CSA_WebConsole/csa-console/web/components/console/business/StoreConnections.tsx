"use client";

import { useState } from "react";
import { connectionLabels, type StoreRecord } from "@/lib/business";
import { channelLabel, whenText } from "@/lib/business-view";
import { connectionsHref, tiktokLineWord } from "@/lib/connections-view";
import type { Me } from "@/lib/identity-contracts";
import { syncLine } from "@/lib/tiktok-connect";
import { TechnicalDetails, TechSwitch } from "../kit";
import { field, Notice, secondary } from "../ui";
import { ManageInConnections } from "../connections/ConnectionCard";
import { Fold, StoreCard } from "./cards";
import { useRecords } from "./useRecords";

/**
 * The Store connections page: each shop's status and sync lines, and the way to the Connections tab,
 * where a shop is connected, reconnected and disconnected.
 */
export default function StoreConnections({ me, query }: { me: Me; query: string }) {
  const [platform, setPlatform] = useState("");
  const params = new URLSearchParams(query);
  if (platform) params.set("platform", platform);
  const stores = useRecords<StoreRecord>(`/console/v1/business/stores?${params}`);
  const [now, setNow] = useState(() => new Date());
  const canManage = me.role === "platform_admin" || me.role === "tenant_admin";
  const href = connectionsHref(me.role === "platform_admin", new URLSearchParams(query).get("client_id"));
  // The line names the TikTok shops, so it waits for the whole list and goes quiet while another
  // marketplace is filtered in (the list then holds none of them).
  const settled = stores.items !== null && !stores.loading && stores.next === null;
  const items = stores.items ?? [];
  const word = settled && (platform === "" || platform === "tiktok_shop") ? tiktokLineWord(stores.items ?? []) : null;

  return <div className="mt-5 space-y-5">
    <section className="card rounded-[14px] p-5 sm:p-6">
      <div className="mb-3">
        <ManageInConnections testId="business-stores-manage" name="TikTok Shop" word={word} href={href} />
      </div>
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
        ? "No online store is connected yet. Connect TikTok Shop on Connections to start syncing its orders and products."
        : "No online store is connected yet. An Admin can connect one."}</p>}
      <ul data-testid="business-stores-list" className="space-y-3">
        {items.map((store) => {
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
          isn&apos;t, the Connect button on Connections says so and stays off.</p>
        <p className="text-sm">Pages follow stable record IDs, not connection or update recency.</p>
      </TechnicalDetails>
    </Fold>
  </div>;
}
