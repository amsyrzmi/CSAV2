import { PollResult } from "./chat-types";

export function mapVoiceErrorCode(rawCode?: string | null): string | null {
  if (!rawCode) return null;
  console.log("error_code:", rawCode);
  if (rawCode.startsWith("AUDIO_FETCH_")) {
    return "The server could not fetch your recording.";
  }
  if (rawCode === "TRANSCRIPTION_TIMEOUT") {
    return "Transcription took too long, please try again.";
  }
  if (rawCode === "VOICE_MIME_UNSUPPORTED") {
    return "This audio format isn't supported.";
  }
  if (rawCode === "INVALID_AUDIO_URI") {
    return "Voice upload configuration error.";
  }
  return null;
}

export async function pollTurn(
  ids: { conversation_id: string; trace_id: string; idempotency_key: string },
  onUpdate: (s: PollResult) => void,
  signal?: AbortSignal,
): Promise<PollResult> {
  const qs = new URLSearchParams(ids).toString();
  const deadline = Date.now() + 90_000;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(`/api/chat/poll?${qs}`, { signal });
      if (res.ok) {
        const s: PollResult = await res.json();
        onUpdate(s);
        if (s.status === "done" || s.status === "held" || s.status === "failed") return s;
      }
    } catch (e: unknown) {
      if (signal?.aborted) throw e; // network blip: just try again
    }
    await new Promise((r) => setTimeout(r, 1500));
  }
  console.log("error_code:", "TRANSCRIPTION_TIMEOUT");
  throw new Error("TRANSCRIPTION_TIMEOUT");
}

export async function submitVoice(
  audioBlob: Blob,
  ids: { conversation_id: string; trace_id: string; idempotency_key: string }
) {
  const formData = new FormData();
  formData.append("audio", audioBlob);
  formData.append("conversation_id", ids.conversation_id);
  formData.append("trace_id", ids.trace_id);
  formData.append("idempotency_key", ids.idempotency_key);

  const res = await fetch("/api/voice", {
    method: "POST",
    body: formData,
  });
  
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    const code = data?.error?.code;
    if (code) {
      const mapped = mapVoiceErrorCode(code);
      if (mapped) throw new Error(mapped);
    }
    throw new Error(data?.error?.message || "Voice submission failed");
  }
  
  return await res.json();
}

