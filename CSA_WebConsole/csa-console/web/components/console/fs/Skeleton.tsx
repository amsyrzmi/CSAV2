/**
 * A grey placeholder block. The shimmer comes from the shared .skeleton class and stops
 * under reduced motion, a hidden tab or a paused-motion preference (see globals.css).
 * Decorative: the list that owns it announces "Loading".
 */
export function Skeleton({ className = "" }: { className?: string }) {
  return <span aria-hidden="true" className={`skeleton block ${className}`} />;
}

/** Shaped like a conversation row: round avatar, a name line and a preview line. */
export function RowSkeleton() {
  return (
    <div aria-hidden="true" className="flex items-center gap-3 px-3 py-3">
      <Skeleton className="size-10 shrink-0 rounded-full" />
      <div className="flex min-w-0 flex-1 flex-col gap-2">
        <Skeleton className="h-3.5 w-2/5" />
        <Skeleton className="h-3 w-4/5" />
      </div>
    </div>
  );
}

export default Skeleton;
