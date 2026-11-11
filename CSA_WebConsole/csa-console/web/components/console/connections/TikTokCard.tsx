"use client";

import { useEffect, useState } from "react";
import type { StoreRecord } from "@/lib/business";
import { offersConnect, storeActions, storeSummary } from "@/lib/business-view";
import { tiktokView, type CardGate } from "@/lib/connections-view";
import type { Me } from "@/lib/identity-contracts";
import { action, Notice, secondary } from "../ui";
import { useRecords } from "../business/useRecords";
import { useTikTokConnect } from "../business/useTikTokConnect";
import { ConnectionCard, ConnectionRow } from "./ConnectionCard";

const SYNC_FAILING = "Syncing is failing. We'll keep trying, and tell you if it keeps failing.";

/**
 * The TikTok Shop card of the Connections tab. A locked card shows only its sentence and a
 * read-only member's card shows a disabled Connect: neither reads the stores. Otherwise one row per
 * TikTok shop with its status, last sync and the buttons the server allows, and a Connect when no
 * shop needs reconnecting. `unavailable` is an arrival with `reason=tiktok_unavailable`.
 * `me` is the signed-in user the tab is drawn for; the gate already holds what its role may do.
 */
export function TikTokCard({ query, gate, unavailable }: {
  me: Me; query: string; gate: CardGate; unavailable: boolean;
}) {
  if (gate.locked) {
    return <ConnectionCard testId="connections-card-tiktok" title="TikTok Shop">
      <ConnectionRow testId="connections-tiktok" name="TikTok Shop" view={null} locked={gate.locked} />
    </ConnectionCard>;
  }
  if (!gate.readStatus) {
    return <ConnectionCard testId="connections-card-tiktok" title="TikTok Shop">
      <ConnectionRow testId="connections-tiktok-new" name="TikTok Shop" view={null} note={gate.note}
        actions={<button className={action} type="button" disabled data-testid="connections-tiktok-connect">
          Connect TikTok Shop</button>} />
    </ConnectionCard>;
  }
  return <TikTokStores query={query} gate={gate} unavailable={unavailable} />;
}

/** The part of the card that reads the stores: the reads and the connect calls live here only. */
function TikTokStores({ query, gate, unavailable }: { query: string; gate: CardGate; unavailable: boolean }) {
  const params = new URLSearchParams(query);
  params.set("platform", "tiktok_shop");
  const stores = useRecords<StoreRecord>(`/console/v1/business/stores?${params}`);
  const [now, setNow] = useState(() => new Date());
  const { busy, problem, unavailable: off, begin, disconnect, markUnavailable } = useTikTokConnect(query, () => {
    setNow(new Date());
    stores.newest();
  });
  // Back from TikTok with "not set up on this server": the card starts with its buttons off.
  useEffect(() => {
    if (unavailable) markUnavailable();
    // markUnavailable only sets a flag; it is new on every render, so it is left out on purpose.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [unavailable]);

  const items = stores.items ?? [];
  const settled = stores.items !== null && !stores.loading && stores.next === null;
  const showConnect = settled && offersConnect(items);
  const connect = <button className={action} type="button" data-testid="connections-tiktok-connect"
    disabled={!gate.canAct || busy || off} onClick={() => void begin()}>Connect TikTok Shop</button>;
  return <ConnectionCard testId="connections-card-tiktok" title="TikTok Shop">
    {stores.error && <Notice error testId="connections-tiktok-error">{stores.error}</Notice>}
    {stores.loading && stores.items === null && <p data-testid="connections-tiktok-loading">Loading...</p>}
    {problem && <Notice error testId="connections-tiktok-problem">{problem}</Notice>}
    {items.map((store) => {
      const buttons = storeActions(store);
      const title = storeSummary(store).title;
      return <ConnectionRow key={store.id} testId={`connections-tiktok-${store.id}`} name={title}
        view={tiktokView(store, now)}
        note={store.sync?.problem === "sync_failing" && store.connection.status !== "missing_shop_id" ? SYNC_FAILING : null}
        actions={(buttons.reconnect || buttons.disconnect) && <>
          {buttons.reconnect && <button className={secondary} type="button" disabled={busy || off}
            aria-label={`Reconnect ${title}`} data-testid={`connections-tiktok-reconnect-${store.id}`}
            onClick={() => void begin(store.id)}>Reconnect</button>}
          {buttons.disconnect && <button className={secondary} type="button" disabled={busy}
            aria-label={`Disconnect ${title}`} data-testid={`connections-tiktok-disconnect-${store.id}`}
            onClick={() => void disconnect(store.id)}>Disconnect</button>}
        </>} />;
    })}
    {showConnect && items.length === 0
      && <ConnectionRow testId="connections-tiktok-new" name="TikTok Shop" view={tiktokView(null, now)}
        note={gate.note} actions={connect} />}
    {showConnect && items.length > 0
      && <div data-testid="connections-tiktok-new" className="flex flex-wrap items-center gap-3">{connect}</div>}
    {settled && gate.note && !(showConnect && items.length === 0)
      && <p data-testid="connections-tiktok-note" className="text-sm text-[color:var(--fs-text-secondary)]">{gate.note}</p>}
  </ConnectionCard>;
}
