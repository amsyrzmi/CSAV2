"use client";

import { useState } from "react";
import { api, ApiError } from "@/lib/api";
import { connectErrorText, navigate } from "@/lib/tiktok-connect";

export const DISCONNECT_CONFIRM = "Stop syncing this shop? Orders stay visible for 30 days, then customer details are removed.";
export const CONNECT_PATH = "/console/v1/business/stores/tiktok/connect";

/**
 * Connect, Reconnect and Disconnect for a TikTok shop, for the Connections tab (the Store connections
 * page only links there). `query` is the tenant query ("client_id=..." for a platform admin,
 * "" otherwise); `onChanged` runs after a disconnect so the caller can read the stores again.
 * Connect and Reconnect end by leaving for TikTok's own page, and only through `navigate`,
 * which follows nothing but a checked authorize URL.
 */
export function useTikTokConnect(query: string, onChanged: () => void) {
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState("");
  const [unavailable, setUnavailable] = useState(false);

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
      onChanged();
    } catch (cause) {
      const failure = cause instanceof ApiError ? cause : null;
      setProblem(connectErrorText(failure?.code ?? "", cause instanceof Error ? cause.message : "Something went wrong."));
    }
    setBusy(false);
  }

  return { busy, problem, unavailable, begin, disconnect, markUnavailable: () => setUnavailable(true) };
}
