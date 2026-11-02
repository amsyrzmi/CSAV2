"use client";

import { useEffect, useState } from "react";
import { whenText, type LastAssistant } from "@/lib/activity-view";
import AgentAvatar from "../fs/AgentAvatar";
import OutcomeBadge from "./OutcomeBadge";

/** The wake plays for 1.5 s (see globals.css); the cards rest a little after it ends. */
const WAKE_MS = 2500;

/**
 * The assistants that ran most recently, one card each. The words and the badge carry how the
 * last run ended; the avatar is decoration. When the cards first appear each assistant that
 * finished well plays its short wake once and then rests; one that needed attention rests from
 * the start, in the rest pose (not dimmed, so its outline stays readable on every theme).
 * Reduced motion, the pause preference and an offscreen strip show the rest pose throughout.
 * `cards` is null until the first read ends; `failed` says that read failed.
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
  return (
    <section data-testid="activity-last-assistants" aria-labelledby="activity-last-title">
      <h2 id="activity-last-title" className="mb-3 font-(family-name:--fs-font-heading) text-base font-semibold">
        Recently active assistants
      </h2>
      {cards === null ? (
        <p className="text-sm text-[color:var(--fs-text-secondary)]">
          {failed ? "Recent assistants are unavailable." : "Loading recent assistants..."}
        </p>
      ) : cards.length === 0 ? (
        <p className="text-sm text-[color:var(--fs-text-secondary)]">Nothing has run yet.</p>
      ) : (
        <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
          {cards.map((card) => (
            <li
              key={card.avatarId}
              data-testid={`activity-card-${card.avatarId}`}
              className="flex min-w-0 items-center gap-3 rounded-[14px] border border-[var(--fs-border)] bg-[var(--fs-surface)] p-4 shadow-[var(--fs-shadow-card)] sm:flex-col sm:items-start sm:gap-2"
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
      )}
    </section>
  );
}
