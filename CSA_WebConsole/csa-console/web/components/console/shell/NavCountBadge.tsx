import { badgeText } from "@/lib/notifications-view";

/**
 * The count pill on a navigation item (today only Inbox, Needs us). It shows nothing for a count
 * that is not a whole number above 0, and it is hidden from assistive technology: the link's own
 * name carries the number (`badgeLinkName`), so it is never read twice.
 */
export function NavCountBadge({
  navKey,
  count,
  className = "",
}: {
  navKey: string;
  count: number | undefined;
  className?: string;
}) {
  const text = badgeText(count);
  if (text === null) return null;
  return (
    <span
      aria-hidden="true"
      data-testid={`nav-badge-${navKey}`}
      className={`inline-flex h-4 min-w-4 shrink-0 items-center justify-center rounded-[var(--fs-radius-round)] bg-[var(--fs-primary)] px-1 text-[length:var(--fs-text-xs)] font-semibold leading-none text-[color:var(--fs-on-primary)] ${className}`}
    >
      {text}
    </span>
  );
}
