"use client";

import { useCallback, useEffect, useRef, useState, type RefObject } from "react";
import { getPlayback } from "@/lib/training-api";
import { Notice } from "../ui";
import { refusalText } from "./shared";

/** New links tried in a row, without the video loading in between, before the page says so. */
const MAX_REFRESHES = 3;
const STUCK =
  "This video would not play. Reload the page and try again. Some browsers cannot play every video.";

/**
 * The video: a native player on the playback URL. A playback URL lasts 15 minutes, so when the
 * player reports an error (an expired link) the page asks for a new one and puts the player back
 * where it was, playing if it was playing. `startAt` (the page's `?t=`) is applied once, when the
 * first metadata is in. The parent keeps the element in `videoRef` to seek it from the transcript.
 */
export default function VideoPlayer({
  tenantId,
  itemId,
  title,
  startAt,
  videoRef,
  onTime,
}: {
  tenantId: string | null;
  itemId: string;
  title: string;
  startAt: number | null;
  videoRef: RefObject<HTMLVideoElement | null>;
  /** The playing position in seconds, as the player reports it. */
  onTime: (seconds: number) => void;
}) {
  const [url, setUrl] = useState<string | null>(null);
  const [problem, setProblem] = useState("");
  // Where to put the player when the next source has its metadata, and the first `?t=` seek.
  const restore = useRef<{ time: number; play: boolean } | null>(null);
  const initial = useRef(startAt);
  const refreshes = useRef(0);
  const asking = useRef(false);

  const load = useCallback(async () => {
    if (asking.current) return;
    asking.current = true;
    try {
      const found = await getPlayback(tenantId, itemId);
      // The local store gives the same address again: the player keeps its failed source until told.
      if (videoRef.current?.getAttribute("src") === found.url) videoRef.current.load();
      setUrl(found.url);
      setProblem("");
    } catch (cause) {
      restore.current = null;
      setProblem(refusalText(cause));
    } finally {
      asking.current = false;
    }
  }, [tenantId, itemId, videoRef]);

  // The first link, when the page opens. Later links come from `failed`.
  useEffect(() => {
    let live = true;
    getPlayback(tenantId, itemId).then(
      (found) => {
        if (live) setUrl(found.url);
      },
      (cause: unknown) => {
        if (live) setProblem(refusalText(cause));
      },
    );
    return () => {
      live = false;
    };
  }, [tenantId, itemId]);

  function seekWhenReady(video: HTMLVideoElement) {
    const target = restore.current ?? (initial.current !== null ? { time: initial.current, play: false } : null);
    restore.current = null;
    initial.current = null;
    if (!target) return;
    const end = Number.isFinite(video.duration) ? video.duration : target.time;
    video.currentTime = Math.min(target.time, end);
    onTime(video.currentTime);
    if (target.play) void video.play().catch(() => undefined);
  }

  function failed(video: HTMLVideoElement) {
    if (refreshes.current >= MAX_REFRESHES) {
      setProblem(STUCK);
      return;
    }
    refreshes.current += 1;
    restore.current = { time: video.currentTime, play: !video.paused && !video.ended };
    void load();
  }

  return (
    <div className="min-w-0 space-y-3">
      {problem && <Notice error testId="training-video-error">{problem}</Notice>}
      {/* The label comes from the title; the player's own controls do the rest. */}
      <video
        ref={videoRef}
        controls
        preload="metadata"
        playsInline
        aria-label={title}
        data-testid="training-video-player"
        className="aspect-video max-h-[70vh] w-full rounded-[var(--fs-radius-control)] bg-[var(--fs-surface-blue)]"
        src={url ?? undefined}
        onLoadedMetadata={(event) => seekWhenReady(event.currentTarget)}
        onLoadedData={() => {
          refreshes.current = 0;
        }}
        onTimeUpdate={(event) => onTime(event.currentTarget.currentTime)}
        onSeeked={(event) => onTime(event.currentTarget.currentTime)}
        onError={(event) => failed(event.currentTarget)}
      />
    </div>
  );
}
