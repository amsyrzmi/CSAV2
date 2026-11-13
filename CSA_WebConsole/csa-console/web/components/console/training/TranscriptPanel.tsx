"use client";

import { useEffect, useId, useMemo, useRef, useState } from "react";
import type { TranscriptLine } from "@/lib/training-api";
import { currentLine, stamp } from "@/lib/training-video";
import { field } from "../ui";
import { muted } from "./shared";

/** How long after the person scrolls the transcript themselves it stops following the video. */
const FOLLOW_PAUSE_MS = 3000;

/**
 * The transcript beside the video: one line per spoken segment with its time as a button that
 * seeks the player and plays; the line being spoken is marked; the search box narrows the lines in
 * the browser (nothing is sent). Every line is a React text child, never HTML or Markdown: the
 * words are speech-to-text output about a file a member uploaded.
 */
export default function TranscriptPanel({
  lines,
  seconds,
  onSeek,
  placeholder,
}: {
  lines: readonly TranscriptLine[];
  /** The player's position. */
  seconds: number;
  /** Seek to this many milliseconds and play. */
  onSeek: (ms: number) => void;
  /** Said instead of the lines while there are none (managers watching a transcript being made). */
  placeholder?: string;
}) {
  const [query, setQuery] = useState("");
  const ids = useId();
  const box = useRef<HTMLDivElement>(null);
  const touched = useRef(0);
  const current = currentLine(lines, seconds);
  const words = query.trim().toLowerCase();
  const shown = useMemo(
    () =>
      lines.flatMap((line, index) =>
        !words || line.text.toLowerCase().includes(words) ? [{ line, index }] : [],
      ),
    [lines, words],
  );

  // Keep the spoken line in view inside the transcript's own box, never moving the page.
  useEffect(() => {
    const container = box.current;
    const row = container?.querySelector<HTMLElement>('[aria-current="true"]');
    if (!container || !row || Date.now() - touched.current < FOLLOW_PAUSE_MS) return;
    if (container.scrollHeight <= container.clientHeight) return;
    container.scrollTop = row.offsetTop - (container.clientHeight - row.offsetHeight) / 2;
  }, [current, shown.length]);

  if (!lines.length)
    return placeholder ? <p className={muted} data-testid="training-transcript-placeholder">{placeholder}</p> : null;

  const pause = () => {
    touched.current = Date.now();
  };

  return (
    <section aria-labelledby={`${ids}-title`} data-testid="training-transcript" className="card flex min-h-0 min-w-0 flex-col gap-3 rounded-[14px] p-4">
      <h2 id={`${ids}-title`} className="font-(family-name:--fs-font-heading) text-base font-semibold">Transcript</h2>
      <div className="text-sm">
        <label htmlFor={`${ids}-search`} className="block">Search the transcript</label>
        <input
          id={`${ids}-search`}
          type="search"
          className={`${field} mt-1 min-w-0 text-base sm:text-sm`}
          data-testid="training-transcript-search"
          autoComplete="off"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
      </div>
      <p className={muted} aria-live="polite" data-testid="training-transcript-count">
        {words ? `Showing ${shown.length} of ${lines.length} lines` : `${lines.length} ${lines.length === 1 ? "line" : "lines"}`}
      </p>
      {shown.length === 0 ? (
        <p className="text-sm" data-testid="training-transcript-none">No lines match. Clear the search to see them all.</p>
      ) : (
        <div
          ref={box}
          className="relative max-h-80 min-w-0 overflow-y-auto overscroll-contain lg:max-h-[28rem]"
          onWheel={pause}
          onTouchMove={pause}
          onPointerDown={pause}
        >
          <ol className="space-y-1">
            {shown.map(({ line, index }) => {
              const spoken = index === current;
              return (
                <li
                  key={index}
                  data-testid="training-transcript-line"
                  aria-current={spoken ? "true" : undefined}
                  className={`flex min-w-0 items-start gap-3 rounded-[var(--fs-radius-control)] px-2 py-1.5 text-sm ${spoken ? "bg-[var(--fs-surface-blue)] font-medium" : ""}`}
                >
                  <button
                    type="button"
                    data-testid="training-transcript-stamp"
                    aria-label={`Play from ${stamp(line.start_ms)}`}
                    className="min-h-8 shrink-0 rounded-[var(--fs-radius-control)] px-1.5 font-medium tabular-nums text-[color:var(--fs-primary)] underline-offset-2 hover:underline pointer-coarse:min-h-11 focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--fs-focus)]"
                    onClick={() => onSeek(line.start_ms)}
                  >
                    {stamp(line.start_ms)}
                  </button>
                  <span className="min-w-0 leading-6 [overflow-wrap:anywhere]">{line.text}</span>
                </li>
              );
            })}
          </ol>
        </div>
      )}
    </section>
  );
}
