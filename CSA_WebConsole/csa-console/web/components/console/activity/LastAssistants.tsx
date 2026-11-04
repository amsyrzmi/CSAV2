"use client";

import { useEffect, useRef, useState } from "react";
import { whenText, type LastAssistant } from "@/lib/activity-view";
import AgentAvatar from "../fs/AgentAvatar";
import OutcomeBadge from "./OutcomeBadge";

/** The wake plays for 1.5 s (see globals.css); the cards rest a little after it ends. */
const WAKE_MS = 2500;
const TITLE = "Recently active assistants";

/**
 * The assistants that ran most recently, one card each. The words and the badge carry how the
 * last run ended; the avatar is decoration. When the cards first appear each assistant that
 * finished well plays its short wake once and then rests; one that needed attention rests from
 * the start, in the rest pose (not dimmed, so its outline stays readable on every theme).
 * Reduced motion, the pause preference and an offscreen card show the rest pose throughout.
 * Below 640 px the cards sit in one sideways-scrolling row (the next card peeks in as the
 * hint); from 640 px up they wrap in a grid. `cards` is null until a read has worked; `failed`
 * says the latest read failed: with no cards that is "unavailable", with cards they are kept and
 * marked as possibly out of date.
 */
export default function LastAssistants({
  cards,
  failed,
  now,
}: {
  cards: LastAssistant[] | null;
  failed: boolean;
  now: Date;
}) {
  const [woken, setWoken] = useState(false);
  const hasCards = (cards?.length ?? 0) > 0;
  useEffect(() => {
    if (!hasCards || woken) return;
    const timer = setTimeout(() => setWoken(true), WAKE_MS);
    return () => clearTimeout(timer);
  }, [hasCards, woken]);

  // The scrolling row is a tab stop only while it really scrolls, so a keyboard can reach it.
  const scroller = useRef<HTMLDivElement>(null);
  const [scrolls, setScrolls] = useState(false);
  useEffect(() => {
    const node = scroller.current;
    if (!node) return;
    const measure = () => setScrolls(node.scrollWidth > node.clientWidth + 1);
    measure();
    const observer = typeof ResizeObserver === "function" ? new ResizeObserver(measure) : null;
    observer?.observe(node);
    window.addEventListener("resize", measure);
    return () => {
      observer?.disconnect();
      window.removeEventListener("resize", measure);
    };
  }, [cards]);

  return (
    <section data-testid="activity-last-assistants">
      <h2 className="mb-3 font-(family-name:--fs-font-heading) text-base font-semibold">{TITLE}</h2>
      {cards !== null && failed && (
        <p data-testid="activity-strip-stale" className="mb-3 text-sm text-[color:var(--fs-text-secondary)]">
          May be out of date
        </p>
      )}
      {cards === null ? (
        <p className="text-sm text-[color:var(--fs-text-secondary)]">
          {failed ? "Recent assistants are unavailable." : "Loading recent assistants..."}
        </p>
      ) : cards.length === 0 ? (
        <p className="text-sm text-[color:var(--fs-text-secondary)]">Nothing has run yet.</p>
      ) : (
        <div
          ref={scroller}
          role="region"
          aria-label={TITLE}
          data-testid="activity-strip-scroll"
          tabIndex={scrolls ? 0 : undefined}
          className="overflow-x-auto overscroll-x-contain pb-2 [-webkit-overflow-scrolling:touch] [scroll-snap-type:x_mandatory] focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--fs-focus)] sm:overflow-visible sm:pb-0 sm:[scroll-snap-type:none]"
        >
          <ul className="flex gap-3 sm:grid sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
            {cards.map((card) => (
              <li
                key={card.avatarId}
                data-testid={`activity-card-${card.avatarId}`}
                className="flex w-[9.5rem] min-w-0 shrink-0 flex-col items-start gap-2 rounded-[14px] border border-[var(--fs-border)] bg-[var(--fs-surface)] p-3 shadow-[var(--fs-shadow-card)] [scroll-snap-align:start] sm:w-auto sm:p-4"
              >
                <AgentAvatar
                  agentId={card.avatarId}
                  size={48}
                  label=""
                  state={card.outcome === "worked" && !woken ? "wake" : "rest"}
                />
                <div className="min-w-0">
                  <p className="text-sm font-semibold [overflow-wrap:anywhere]">{card.title}</p>
                  <p className="mt-0.5 text-sm text-[color:var(--fs-text-secondary)]">
                    finished {whenText(card.finishedAt, now)}
                  </p>
                  <div className="mt-2">
                    <OutcomeBadge outcome={card.outcome} testId={`activity-card-${card.avatarId}-outcome`} />
                  </div>
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
