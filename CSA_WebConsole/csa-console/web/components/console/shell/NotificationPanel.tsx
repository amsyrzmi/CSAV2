"use client";

import Link from "next/link";
import { agoText, isUnread, notificationLink, renderNotification } from "@/lib/notifications-view";
import type { NotificationItem } from "@/lib/notifications-view";
import { companyDateTime } from "@/lib/inbox-queue";
import { consoleHref } from "@/lib/navigation";
import StatusLabel from "@/components/console/fs/StatusLabel";
import { POPOVER } from "./ThemeControl";
import type { NotificationsState } from "./useNotifications";

const TEXT_BUTTON =
  "inline-flex min-h-10 items-center rounded-[var(--fs-radius-control)] px-3 text-[length:var(--fs-text-sm)] font-medium text-[color:var(--fs-primary)] hover:bg-[var(--fs-tile)] focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--fs-focus)] disabled:cursor-not-allowed disabled:text-[color:var(--fs-disabled-text)] disabled:hover:bg-transparent pointer-coarse:min-h-11";
const ROW_FOCUS =
  "focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-[color:var(--fs-focus)]";

function RowBody({ item, now }: { item: NotificationItem; now: Date }) {
  const { title, detail } = renderNotification(item);
  const unread = isUnread(item);
  const absolute = companyDateTime(item.created_at);
  return (
    <span className="flex min-w-0 items-start gap-2">
      <span className="min-w-0 flex-1">
        {unread && <span className="sr-only">Unread. </span>}
        <span
          data-testid="notification-title"
          className={`block break-words text-[length:var(--fs-text-sm)] ${unread ? "font-semibold" : "font-normal"}`}
        >
          {title}
        </span>
        <span
          data-testid="notification-detail"
          className="block break-words text-[length:var(--fs-text-xs)] text-[color:var(--fs-text-secondary)]"
        >
          {detail}
        </span>
        <time
          dateTime={item.created_at}
          title={absolute || undefined}
          className="block text-[length:var(--fs-text-xs)] text-[color:var(--fs-text-secondary)]"
        >
          {agoText(now, item.created_at)}
        </time>
      </span>
      {unread && (
        <span aria-hidden="true" className="shrink-0">
          <StatusLabel tone="neutral">New</StatusLabel>
        </span>
      )}
    </span>
  );
}

/**
 * The open bell panel: a header (title and "Mark all as read"), then the loading, failed, empty
 * or list state. Presentational: `NotificationBell` owns the hook. A row with an Inbox link is a
 * real link whose click marks that row read; any other row is plain text with a "Mark as read"
 * button while it is unread.
 */
export function NotificationPanel({
  id,
  state,
  items,
  unread,
  error,
  notice,
  loadedAt,
  onLoad,
  onMarkRead,
  onMarkAll,
  onNavigate,
}: {
  id: string;
  state: NotificationsState;
  items: NotificationItem[];
  unread: number;
  error: string;
  notice: string;
  loadedAt: number;
  onLoad: () => void;
  onMarkRead: (ids: string[]) => void;
  onMarkAll: () => void;
  onNavigate: () => void;
}) {
  const now = new Date(loadedAt);
  return (
    <div
      id={id}
      role="group"
      aria-label="Notifications"
      data-testid="shell-notifications-panel"
      className={`${POPOVER} w-[24rem] max-w-[calc(100vw-2rem)] max-md:right-4`}
    >
      <div className="flex items-center justify-between gap-2 px-3 py-1">
        <p className="min-w-0 text-[length:var(--fs-text-sm)] font-semibold">Notifications</p>
        <button
          type="button"
          disabled={state !== "ready" || unread === 0}
          onClick={onMarkAll}
          className={`${TEXT_BUTTON} shrink-0`}
        >
          Mark all as read
        </button>
      </div>
      <p role="status" className="px-3 text-[length:var(--fs-text-xs)] text-[color:var(--fs-text-secondary)]">
        {notice}
      </p>
      {error && (
        <p role="alert" className="px-3 py-1 text-[length:var(--fs-text-sm)] text-[color:var(--fs-danger)]">
          {error}
        </p>
      )}
      {state === "loading" && (
        <p className="px-3 py-3 text-[length:var(--fs-text-sm)] text-[color:var(--fs-text-secondary)]">
          Loading notifications…
        </p>
      )}
      {state === "failed" && (
        <div className="px-3 py-3">
          <p className="text-[length:var(--fs-text-sm)] text-[color:var(--fs-text-secondary)]">
            Notifications did not load.
          </p>
          <button type="button" onClick={onLoad} className={`${TEXT_BUTTON} -ml-3`}>
            Try again
          </button>
        </div>
      )}
      {state === "ready" && items.length === 0 && (
        <p className="px-3 py-3 text-[length:var(--fs-text-sm)] text-[color:var(--fs-text-secondary)]">
          No notifications yet. Reminders about waiting customers appear here.
        </p>
      )}
      {state === "ready" && items.length > 0 && (
        <ul className="max-h-[28rem] overflow-y-auto">
          {items.map((item) => {
            const link = notificationLink(item);
            const open = isUnread(item);
            return (
              <li
                key={item.id}
                data-testid="notification-row"
                className="border-t border-[color:var(--fs-border)] first:border-t-0"
              >
                {link ? (
                  <Link
                    href={consoleHref(false, "inbox", { conversation: link.conversation })}
                    onClick={() => {
                      if (open) onMarkRead([item.id]);
                      onNavigate();
                    }}
                    className={`block rounded-[var(--fs-radius-control)] px-3 py-2 hover:bg-[var(--fs-tile)] ${ROW_FOCUS}`}
                  >
                    <RowBody item={item} now={now} />
                  </Link>
                ) : (
                  <div className="px-3 py-2">
                    <RowBody item={item} now={now} />
                    {open && (
                      <button
                        type="button"
                        onClick={() => onMarkRead([item.id])}
                        className={`${TEXT_BUTTON} -ml-3`}
                      >
                        Mark as read
                      </button>
                    )}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
