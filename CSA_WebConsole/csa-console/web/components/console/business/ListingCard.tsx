"use client";

import type { Listing } from "@/lib/business";
import {
  isListingOut, listingDetailGroups, listingSku, listingStatus, listingSummary, sameSkuLine, whenText,
} from "@/lib/business-view";
import { syncLine } from "@/lib/tiktok-connect";
import { RecordCard } from "./cards";

/**
 * One TikTok Shop product as the sync copied it: product, variant, SKU, price, TikTok's own
 * stock and status, when it was synced, and our product with the same SKU when there is one.
 * Provider text is shown as plain text; the product picture is not shown on purpose.
 */
export function ListingCard({ listing, now, onOpen }: {
  listing: Listing; now: Date;
  /** Told when the card opens and closes, so the page can tell the assistant (id only). */
  onOpen?: (id: string, open: boolean) => void;
}) {
  const summary = listingSummary(listing);
  const status = listingStatus(listing);
  const same = sameSkuLine(listing);
  return (
    <RecordCard
      testId={`business-listing-${listing.id}`}
      toggleLabel={`TikTok product ${listing.product_name}`}
      title={summary.title}
      subtitle={summary.subtitle}
      labels={[
        { testId: "status", tone: status.tone, text: status.label },
        ...(isListingOut(listing) ? [{ testId: "stock", tone: "attention" as const, text: "Out of stock" }] : []),
      ]}
      groups={listingDetailGroups(listing)}
      onOpen={onOpen ? (open) => onOpen(listing.id, open) : undefined}
      extra={<div className="mt-1.5 text-sm text-[color:var(--fs-text-secondary)]">
        <p data-testid={`business-listing-${listing.id}-sync`}>{syncLine(null, listing.synced_at, now)}</p>
        {same && <p data-testid={`business-listing-${listing.id}-same-sku`}>{same}</p>}
      </div>}
    >
      <p className="break-words font-semibold">{listingSku(listing) ?? "No SKU shared"} · {listing.product_name}</p>
      <p>{listing.currency} {listing.price ?? "no price"} · TikTok status: {listing.product_status ?? "Not given"}</p>
      <p>TikTok stock: {listing.stock_qty ?? "Not given"}</p>
      <p>Synced from TikTok: {whenText(listing.synced_at)}</p>
    </RecordCard>
  );
}
