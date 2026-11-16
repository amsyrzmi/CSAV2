"use client";

import Link from "next/link";
import { useId, useState } from "react";
import { thumbnailUrl, type TrainingItem, type TrainingList } from "@/lib/training-api";
import { itemFacts } from "@/lib/training-form";
import { categoriesOf, filterItems, videoStateText } from "@/lib/training-view";
import { action, field, secondary } from "../ui";
import { muted } from "./shared";

const ring =
  "rounded-[var(--fs-radius-control)] focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--fs-focus)]";

// Every row's picture box is this size, image or placeholder, so a picture arriving late (or
// failing) never moves the row. 16:9, the shape of most videos; a thumbnail of another shape is
// cropped to fit.
const THUMB_WIDTH = 112;
const THUMB_HEIGHT = 63;
const thumbBox = "h-[63px] w-[112px] shrink-0 overflow-hidden rounded-[var(--fs-radius-control)]";

/**
 * The row's first child: a video's generated preview, else a placeholder (a PDF, a video uploaded
 * before previews, one still being processed, or a preview that would not load). Decorative: the
 * title sits next to it, so the image has empty alt text and the placeholder is hidden from
 * assistive technology.
 */
function ItemThumbnail({ tenantId, item }: { tenantId: string | null; item: TrainingItem }) {
  // The version that failed to load; a new version (a changed picture) is tried afresh.
  const [failed, setFailed] = useState<string | null>(null);
  const version = item.thumbnail?.version ?? null;
  if (version !== null && failed !== version) {
    return (
      // eslint-disable-next-line @next/next/no-img-element -- a same-origin route, revalidated on its ETag
      <img
        src={thumbnailUrl(tenantId, item.id, version)}
        alt=""
        width={THUMB_WIDTH}
        height={THUMB_HEIGHT}
        loading="lazy"
        decoding="async"
        data-testid={`training-thumbnail-${item.id}`}
        className={`${thumbBox} bg-[var(--fs-surface-blue)] object-cover`}
        onError={() => setFailed(version)}
      />
    );
  }
  return (
    <div
      aria-hidden="true"
      data-testid={`training-thumbnail-placeholder-${item.id}`}
      className={`${thumbBox} flex items-center justify-center bg-[var(--fs-surface-blue)] text-xs font-semibold tracking-wide text-[color:var(--fs-text-secondary)]`}
    >
      {item.kind === "video" ? "VIDEO" : "PDF"}
    </div>
  );
}

/**
 * The company's PDFs and videos, newest first (the server's order), with a category filter and a
 * search by title. Managers get "Add a PDF or video" and, on each row, "Edit details" and "Remove",
 * and see where a video is in processing; readers get none of that. An empty library says what
 * will appear; a filter with no match says how to undo it. Each row starts with the video's
 * preview, or a placeholder.
 */
export default function ItemList({
  tenantId,
  list,
  hrefFor,
  onAdd,
  onEdit,
  onRemove,
}: {
  /** Null for a tenant member; the chosen company for a platform admin (the preview names it). */
  tenantId: string | null;
  list: TrainingList;
  hrefFor: (id: string) => string;
  onAdd: () => void;
  onEdit: (item: TrainingItem) => void;
  onRemove: (item: TrainingItem) => void;
}) {
  const [category, setCategory] = useState("");
  const [query, setQuery] = useState("");
  const ids = useId();
  const manage = list.can_manage;
  const categories = categoriesOf(list.items);
  // A category that has gone (its last PDF removed) stops filtering.
  const active = categories.includes(category) ? category : "";
  const shown = filterItems(list.items, { category: active, query });

  if (!list.items.length) {
    return (
      <div data-testid="training-empty" className="card flex flex-col items-start gap-3 rounded-[14px] p-5">
        <p className="text-sm">PDFs and videos your company adds will appear here.</p>
        {manage && (
          <button type="button" className={action} data-testid="training-add" onClick={onAdd}>Add a PDF or video</button>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-3">
        <div className="min-w-0 flex-1 basis-56 text-sm">
          <label htmlFor={`${ids}-search`} className="block">Search by title</label>
          <input
            id={`${ids}-search`}
            type="search"
            className={`${field} mt-1 min-w-0`}
            data-testid="training-search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </div>
        {/* Full width on a phone (its own line under the search); a fixed width beside it above. */}
        <div className="w-full min-w-0 text-sm sm:w-56">
          <label htmlFor={`${ids}-category`} className="block">Category</label>
          <select
            id={`${ids}-category`}
            className={`${field} mt-1`}
            data-testid="training-filter"
            value={active}
            onChange={(event) => setCategory(event.target.value)}
          >
            <option value="">All categories</option>
            {categories.map((name) => <option key={name} value={name}>{name}</option>)}
          </select>
        </div>
        {manage && (
          <button type="button" className={action} data-testid="training-add" onClick={onAdd}>Add a PDF or video</button>
        )}
      </div>
      <p className={muted} aria-live="polite" data-testid="training-count">
        {shown.length === list.items.length
          ? `${list.items.length} ${list.items.length === 1 ? "item" : "items"}`
          : `Showing ${shown.length} of ${list.items.length} items`}
      </p>
      {shown.length === 0 ? (
        <div data-testid="training-no-match" className="card flex flex-col items-start gap-3 rounded-[14px] p-5">
          <p className="text-sm">Nothing matches. Clear the search or choose another category.</p>
          <button
            type="button"
            className={secondary}
            data-testid="training-clear-filter"
            onClick={() => {
              setQuery("");
              setCategory("");
            }}
          >
            Show everything
          </button>
        </div>
      ) : (
        <ul data-testid="training-list" className="space-y-3">
          {shown.map((item) => (
            <li
              key={item.id}
              data-testid={`training-item-${item.id}`}
              className="card flex min-w-0 flex-col gap-3 rounded-[14px] p-4 sm:flex-row sm:items-start sm:justify-between"
            >
              <ItemThumbnail tenantId={tenantId} item={item} />
              <div className="min-w-0 flex-1">
                <h2 className="font-(family-name:--fs-font-heading) text-base font-semibold [overflow-wrap:anywhere]">
                  <Link
                    href={hrefFor(item.id)}
                    data-testid={`training-open-${item.id}`}
                    className={`${ring} text-[color:var(--fs-primary)] underline-offset-2 hover:underline`}
                  >
                    {item.title}
                  </Link>
                </h2>
                {item.description && <p className={`mt-1 line-clamp-2 ${muted}`}>{item.description}</p>}
                <ul className={`mt-2 flex flex-wrap gap-x-3 gap-y-1 ${muted}`}>
                  {itemFacts(item).map((fact, index) => <li key={index}>{fact}</li>)}
                </ul>
                {manage && item.kind === "video" && videoStateText(item) && (
                  <p className={`mt-2 font-medium ${item.status === "failed" ? "text-[color:var(--fs-danger)]" : ""} text-sm [overflow-wrap:anywhere]`}
                    data-testid={`training-state-${item.id}`}>
                    {videoStateText(item)}
                  </p>
                )}
              </div>
              {manage && (
                <div className="flex shrink-0 flex-wrap gap-2">
                  {/* The name starts with the visible words and says which item, as every row has both. */}
                  <button type="button" className={secondary} data-testid="training-edit"
                    aria-label={`Edit details: ${item.title}`} onClick={() => onEdit(item)}>
                    Edit details
                  </button>
                  <button type="button" className={secondary} data-testid="training-remove"
                    aria-label={`Remove ${item.kind === "video" ? "video" : "PDF"}: ${item.title}`}
                    onClick={() => onRemove(item)}>
                    Remove {item.kind === "video" ? "video" : "PDF"}
                  </button>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
