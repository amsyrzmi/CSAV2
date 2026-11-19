"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";
import { Bell } from "lucide-react";
import { badgeText } from "@/lib/notifications-view";
import { useMotionAllowed } from "@/components/console/fs/useMotionAllowed";
import { NotificationPanel } from "./NotificationPanel";
import { BAR_BUTTON } from "./ThemeControl";
import { useDismiss } from "./useDismiss";
import { useNotifications } from "./useNotifications";

/**
 * The notification bell between the theme control and the user menu. `unread` is the shell's
 * polled count (null while unknown: no badge); `onMarked` re-reads it after a mark-read. The
 * badge is hidden from assistive technology and the number is in the button's name instead.
 * Opening the panel marks nothing read. The badge nudges (re-keyed span, rises only) when the
 * count rises and motion is allowed.
 */
export function NotificationBell({
  unread,
  onMarked,
}: {
  unread: number | null;
  onMarked: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [nudge, setNudge] = useState(0);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const lastCount = useRef<number | null>(null);
  const panelId = useId();
  const motion = useMotionAllowed();
  const close = useCallback(() => setOpen(false), []);
  const dismiss = useDismiss({ open, rootRef, triggerRef, onClose: close });
  const list = useNotifications({ enabled: open, count: unread, onMarked });

  useEffect(() => {
    const before = lastCount.current;
    lastCount.current = unread;
    if (motion && typeof before === "number" && typeof unread === "number" && unread > before) {
      setNudge((value) => value + 1);
    }
  }, [unread, motion]);

  function toggle() {
    if (!open) void list.load();
    setOpen(!open);
  }

  const text = badgeText(unread);
  const name = text === null ? "Notifications" : `Notifications, ${unread} unread`;
  return (
    // Static below 768 px so the panel spans the top bar instead of the bell's own box.
    <div ref={rootRef} className="md:relative" onKeyDown={dismiss.onKeyDown}>
      <button
        ref={triggerRef}
        type="button"
        data-testid="shell-notifications"
        aria-label={name}
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        onClick={toggle}
        className={`${BAR_BUTTON} relative`}
      >
        <Bell size={18} aria-hidden="true" />
        {text !== null && (
          <span
            key={`nudge-${nudge}`}
            aria-hidden="true"
            data-testid="shell-notifications-count"
            className={`absolute -right-1.5 -top-1.5 inline-flex h-4 min-w-4 items-center justify-center rounded-[var(--fs-radius-round)] bg-[var(--fs-primary)] px-1 text-[length:var(--fs-text-xs)] font-semibold leading-none text-[color:var(--fs-on-primary)] ${nudge > 0 ? "fs-nudge" : ""}`}
          >
            {text}
          </span>
        )}
      </button>
      {open && (
        <NotificationPanel
          id={panelId}
          state={list.state}
          items={list.items}
          unread={list.unread}
          error={list.error}
          notice={list.notice}
          loadedAt={list.loadedAt}
          onLoad={() => void list.load()}
          onMarkRead={list.markRead}
          onMarkAll={() => void list.markAll()}
          onNavigate={(keyboard) => {
            close();
            // The focused link unmounts with the panel; keep keyboard focus on the bell.
            if (keyboard) triggerRef.current?.focus();
          }}
        />
      )}
    </div>
  );
}
