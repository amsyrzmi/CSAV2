"use client";

import Link from "next/link";

type Tile = {
  key: string;
  label: string;
  value: number | null;
  /** True while the read is still on its way: the tile says so instead of "Not available". */
  loading?: boolean;
  caption?: string;
  pressed?: boolean;
  onPress?: () => void;
  href?: string;
  testId: string;
};

const base =
  "relative flex min-h-16 min-w-0 flex-col items-start justify-center rounded-[var(--fs-radius-panel)] border bg-[var(--fs-surface)] px-3 py-2 text-left " +
  "text-[color:var(--fs-text)] pointer-coarse:min-h-16 " +
  "transition-[background-color,border-color] duration-150 ease-(--fs-ease-out) motion-reduce:transition-none " +
  "focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--fs-focus)]";
const idle = "border-[color:var(--fs-border-control)] hover:border-[color:var(--fs-primary)]";
// The inset ring doubles the pressed border to 2px without changing the tile's size.
const pressedClass =
  "border-[color:var(--fs-primary)] bg-[var(--fs-surface-blue)] shadow-[inset_0_0_0_1px_var(--fs-primary)]";

function Body({ tile }: { tile: Tile }) {
  return (
    <>
      {tile.pressed ? (
        <svg aria-hidden="true" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor"
          strokeWidth="3" strokeLinecap="round" strokeLinejoin="round"
          className="absolute top-2 right-2 text-[color:var(--fs-primary)]">
          <path d="m5 12 5 5 9-10" />
        </svg>
      ) : null}
      {/* Sized on spans: the tokens' unlayered `font: inherit` on buttons beats utilities. */}
      {tile.loading ? (
        <span data-testid={`${tile.testId}-value`}
          className="text-[length:var(--fs-text-md)] text-[color:var(--fs-text-secondary)]">Loading...</span>
      ) : tile.value === null ? (
        <span data-testid={`${tile.testId}-value`}
          className="text-[length:var(--fs-text-md)] font-semibold">Not available</span>
      ) : (
        <span data-testid={`${tile.testId}-value`}
          className="font-(family-name:--fs-font-heading) text-[length:var(--fs-text-xl)] leading-tight font-semibold">
          {tile.value}
        </span>
      )}
      <span className="text-[length:var(--fs-text-md)] [overflow-wrap:anywhere]">{tile.label}</span>
      {tile.caption ? (
        <span data-testid={`${tile.testId}-caption`}
          className="text-[length:var(--fs-text-sm)] text-[color:var(--fs-text-secondary)] [overflow-wrap:anywhere]">
          {tile.caption}
        </span>
      ) : null}
    </>
  );
}

/**
 * A row of large-number tiles: two columns on phones, four from 768 px. A tile with `onPress`
 * is a toggle button (`aria-pressed`, a check mark and a doubled border when pressed), one with
 * `href` is a link, and a tile whose read failed (`value` null) says "Not available", never 0.
 */
export default function SummaryTiles({ ariaLabel, tiles, testId }: { ariaLabel: string; tiles: Tile[]; testId?: string }) {
  return (
    <div role="group" aria-label={ariaLabel} data-testid={testId} className="mb-3 grid grid-cols-2 gap-3 md:grid-cols-4">
      {tiles.map((tile) => {
        const className = `${base} ${tile.pressed ? pressedClass : idle}`;
        if (tile.href && !tile.onPress) {
          return <Link key={tile.key} href={tile.href} data-testid={tile.testId} className={className}><Body tile={tile} /></Link>;
        }
        if (!tile.onPress) {
          return <div key={tile.key} data-testid={tile.testId} className={className}><Body tile={tile} /></div>;
        }
        return (
          <button key={tile.key} type="button" data-testid={tile.testId} className={className}
            aria-pressed={Boolean(tile.pressed)} onClick={tile.onPress}>
            <Body tile={tile} />
          </button>
        );
      })}
    </div>
  );
}
