"use client";

import { useEffect, useRef, useState } from "react";
import { getTranscript, retryItem, type TrainingItem, type TrainingTranscript } from "@/lib/training-api";
import { canRetry, HEVC_NOTE, refusalCopy, videoStateText } from "@/lib/training-view";
import { action, Notice } from "../ui";
import { codeOf, refusalText } from "./shared";
import { VideoSummary } from "./SummaryPanel";
import TranscriptPanel from "./TranscriptPanel";
import VideoPlayer from "./VideoPlayer";

const WHILE_MAKING = "The transcript will appear here as it is made.";

/**
 * A video's page body: for managers, where processing is (with "Try again" for a failure worth
 * another go) and the note about browsers that cannot play the picture; then the player with the
 * transcript beside it (below it on a phone), then the summary. Readers only ever meet a ready
 * video. The player and the transcript share one position: a transcript line seeks the player,
 * and the player's time marks the spoken line.
 */
export default function VideoSection({
  tenantId,
  item,
  manage,
  startAt,
  onChanged,
}: {
  tenantId: string | null;
  item: TrainingItem;
  manage: boolean;
  /** The page's `?t=` in seconds. */
  startAt: number | null;
  /** The item changed (a retry) and the list should be read again. */
  onChanged: () => void;
}) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [seconds, setSeconds] = useState(0);
  const [transcript, setTranscript] = useState<TrainingTranscript | null>(null);
  const [transcriptError, setTranscriptError] = useState<string | null>(null);
  const [retrying, setRetrying] = useState(false);
  const [retryError, setRetryError] = useState("");
  const running = useRef(false);
  const { id, status } = item;
  const made = item.processing?.chunks_done ?? -1;
  const readable = status !== "uploading" && status !== "uploaded";
  // A file that failed for a reason in the file itself is deleted: nothing to play.
  const playable = status !== "uploading" && (status !== "failed" || canRetry(item));
  const state = videoStateText(item);

  // The lines made so far, read again as more parts are done and once more when it is ready.
  useEffect(() => {
    if (!readable) return;
    let live = true;
    getTranscript(tenantId, id).then(
      (found) => {
        if (!live) return;
        setTranscript(found);
        setTranscriptError(null);
      },
      (cause: unknown) => {
        if (live) setTranscriptError(codeOf(cause));
      },
    );
    return () => {
      live = false;
    };
  }, [tenantId, id, status, made, readable]);

  function seek(ms: number) {
    const video = videoRef.current;
    if (!video) return;
    video.currentTime = ms / 1000;
    setSeconds(video.currentTime);
    void video.play().catch(() => undefined);
  }

  async function retry() {
    if (running.current) return;
    running.current = true;
    setRetrying(true);
    setRetryError("");
    try {
      await retryItem(tenantId, id);
      onChanged();
    } catch (cause) {
      setRetryError(refusalText(cause));
      onChanged();
    } finally {
      running.current = false;
      setRetrying(false);
    }
  }

  const lines = transcript?.segments ?? [];
  const waiting = manage && status !== "ready" && status !== "failed" ? WHILE_MAKING : undefined;
  // Without a transcript the player has the full width.
  const beside = lines.length > 0 || waiting !== undefined;
  return (
    <div className="min-w-0 space-y-5" data-testid="training-video-section">
      {manage && state && (
        <div className="space-y-3">
          <Notice error={status === "failed"} testId="training-video-state">{state}</Notice>
          {retryError && <Notice error testId="training-retry-error">{retryError}</Notice>}
          {canRetry(item) && (
            <button type="button" className={action} disabled={retrying} aria-busy={retrying}
              data-testid="training-retry" onClick={() => void retry()}>
              Try again
            </button>
          )}
        </div>
      )}
      {manage && item.notice === "hevc" && <Notice warning testId="training-hevc-note">{HEVC_NOTE}</Notice>}
      {transcriptError !== null && lines.length === 0 && (
        <Notice error testId="training-transcript-error">{refusalCopy(transcriptError)}</Notice>
      )}
      {playable && (
        <div className={`grid min-w-0 gap-5 ${beside ? "lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]" : ""}`}>
          <VideoPlayer tenantId={tenantId} itemId={id} title={item.title} startAt={startAt}
            videoRef={videoRef} onTime={setSeconds} />
          <TranscriptPanel lines={lines} seconds={seconds} onSeek={seek}
            placeholder={waiting} />
        </div>
      )}
      {!playable && lines.length > 0 && <TranscriptPanel lines={lines} seconds={0} onSeek={() => undefined} />}
      <VideoSummary key={id} tenantId={tenantId} item={item} speech={transcript?.speech ?? null}
        speechFailed={transcriptError !== null} />
    </div>
  );
}
