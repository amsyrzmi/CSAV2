"use client";

import Link from "next/link";
import { useId, useState } from "react";
import type { TrainingItem, TrainingList } from "@/lib/training-api";
import { itemFacts } from "@/lib/training-form";
import { categoriesOf, filterItems } from "@/lib/training-view";
import { action, field, secondary } from "../ui";
import { muted } from "./shared";

const ring =
  "rounded-[var(--fs-radius-control)] focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--fs-focus)]";

/**
 * The company's PDFs, newest first (the server's order), with a category filter and a search by
 * title. Managers get "Add a PDF" and, on each row, "Edit details" and "Remove PDF"; readers get
 * neither. An empty library says what will appear; a filter with no match says how to undo it.
 */
export default function ItemList({
  list,
  hrefFor,
  onAdd,
  onEdit,
  onRemove,
}: {
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
        <p className="text-sm">PDFs your company adds will appear here.</p>
        {manage && (
          <button type="button" className={action} data-testid="training-add" onClick={onAdd}>Add a PDF</button>
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
          <button type="button" className={action} data-testid="training-add" onClick={onAdd}>Add a PDF</button>
        )}
      </div>
      <p className={muted} aria-live="polite" data-testid="training-count">
        {shown.length === list.items.length
          ? `${list.items.length} ${list.items.length === 1 ? "PDF" : "PDFs"}`
          : `Showing ${shown.length} of ${list.items.length} PDFs`}
      </p>
      {shown.length === 0 ? (
        <div data-testid="training-no-match" className="card flex flex-col items-start gap-3 rounded-[14px] p-5">
          <p className="text-sm">No PDFs match. Clear the search or choose another category.</p>
          <button
            type="button"
            className={secondary}
            data-testid="training-clear-filter"
            onClick={() => {
              setQuery("");
              setCategory("");
            }}
          >
            Show all PDFs
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
              </div>
              {manage && (
                <div className="flex shrink-0 flex-wrap gap-2">
                  {/* The name starts with the visible words and says which PDF, as every row has both. */}
                  <button type="button" className={secondary} data-testid="training-edit"
                    aria-label={`Edit details: ${item.title}`} onClick={() => onEdit(item)}>
                    Edit details
                  </button>
                  <button type="button" className={secondary} data-testid="training-remove"
                    aria-label={`Remove PDF: ${item.title}`} onClick={() => onRemove(item)}>
                    Remove PDF
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
