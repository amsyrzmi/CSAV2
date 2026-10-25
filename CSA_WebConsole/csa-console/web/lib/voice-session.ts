/** Bounded utterances and interruptible playback using installed browser voices. */
export function voiceRequestSignal(signal: AbortSignal, timeout = 15_000, deadline = Infinity): AbortSignal {
  const remaining = Math.min(timeout, deadline - Date.now());
  return AbortSignal.any([signal, remaining <= 0
    ? AbortSignal.abort(new DOMException("Voice request deadline exceeded.", "TimeoutError"))
    : AbortSignal.timeout(Math.ceil(remaining))]);
}

export class SpeechBoundary {
  private started: number | null = null;
  private quiet: number | null = null;
  private loudFrames = 0;
  sample(rms: number, now: number): boolean {
    if (rms >= 0.025) {
      this.loudFrames++;
      if (this.loudFrames >= 3) this.started ??= now;
      this.quiet = null;
    } else {
      this.loudFrames = 0;
      if (this.started !== null) this.quiet ??= now;
    }
    return this.started !== null && this.quiet !== null && now - this.quiet >= 1200;
  }
  get heardSpeech() { return this.started !== null; }
}

export async function captureUtterance(signal: AbortSignal, options: {
  onSpeechStart?: () => void; waitForSpeech?: boolean;
} = {}): Promise<Blob> {
  if (!window.isSecureContext || !navigator.mediaDevices || typeof MediaRecorder === "undefined") throw new Error("A secure browser with microphone recording is required.");
  const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
  if (signal.aborted) { stream.getTracks().forEach(track => track.stop()); throw signal.reason; }
  let context: AudioContext | null = null;
  try {
    const mime = ["audio/webm;codecs=opus", "audio/mp4", "audio/ogg;codecs=opus"].find(value => MediaRecorder.isTypeSupported(value));
    if (!mime) throw new Error("No supported audio recording format.");
    context = new AudioContext();
    await context.resume();
    const analyser = context.createAnalyser(); analyser.fftSize = 2048;
    context.createMediaStreamSource(stream).connect(analyser);
    const media = new MediaRecorder(stream, { mimeType: mime });
    const samples = new Float32Array(analyser.fftSize);
    return await new Promise<Blob>((resolve, reject) => {
      const chunks: Blob[] = []; let bytes = 0;
      const boundary = new SpeechBoundary(); const beginning = performance.now();
      function clean() { clearInterval(timer); signal.removeEventListener("abort", abort); }
      function fail(error: unknown) {
        clean(); media.onstop = null; media.ondataavailable = null;
        if (media.state !== "inactive") media.stop(); reject(error);
      }
      function abort() { fail(signal.reason ?? new Error("Voice chat stopped.")); }
      media.onerror = () => fail(new Error("Microphone recording failed."));
      media.ondataavailable = event => {
        bytes += event.data.size;
        if (bytes > 8 * 1024 * 1024) fail(new Error("Recording exceeds 8 MB."));
        else chunks.push(event.data);
      };
      media.onstop = () => {
        clean();
        if (!boundary.heardSpeech) reject(new Error("No speech detected. Start again when ready."));
        else resolve(new Blob(chunks, { type: mime }));
      };
      signal.addEventListener("abort", abort, { once: true });
      media.start(500);
      const timer = setInterval(() => {
        analyser.getFloatTimeDomainData(samples);
        const rms = Math.sqrt(samples.reduce((sum, value) => sum + value * value, 0) / samples.length);
        const now = performance.now();
        const previouslyHeard = boundary.heardSpeech;
        const finished = boundary.sample(rms, now);
        if (!previouslyHeard && boundary.heardSpeech) options.onSpeechStart?.();
        if (finished || now - beginning >= 60_000 || (!options.waitForSpeech && !boundary.heardSpeech && now - beginning >= 12_000)) {
          if (media.state === "recording") media.stop();
        }
      }, 100);
      if (signal.aborted) abort();
    });
  } finally { stream.getTracks().forEach(track => track.stop()); await context?.close(); }
}

export async function installedVoice(language: string, signal: AbortSignal): Promise<SpeechSynthesisVoice> {
  const synthesis = window.speechSynthesis;
  if (!synthesis) throw new Error("Browser speech playback is unavailable.");
  const select = () => synthesis.getVoices().find(value => value.localService && value.lang.toLowerCase().startsWith(language));
  let voice = select();
  if (!voice && synthesis.getVoices().length === 0) {
    await new Promise<void>((resolve, reject) => {
      function clean() { clearTimeout(timer); synthesis.removeEventListener("voiceschanged", changed); signal.removeEventListener("abort", abort); }
      function changed() { clean(); resolve(); }
      function abort() { clean(); reject(signal.reason); }
      const timer = setTimeout(changed, 2000);
      synthesis.addEventListener("voiceschanged", changed, { once: true });
      signal.addEventListener("abort", abort, { once: true });
      if (signal.aborted) abort();
    });
    voice = select();
  }
  signal.throwIfAborted();
  if (!voice) throw new Error(`No installed ${language === "ms" ? "Malay" : "English"} voice. Install one for this playback language before starting.`);
  return voice;
}

export async function playLocalReply(text: string, language: string, signal: AbortSignal): Promise<void> {
  const synthesis = window.speechSynthesis;
  const voice = await installedVoice(language, signal);
  // Short chunks avoid browser limits without truncating the reply.
  const chunks = text.match(/[\s\S]{1,220}(?:\s|$)|\S{1,220}/g) ?? [];
  for (const chunk of chunks) {
    signal.throwIfAborted();
    await new Promise<void>((resolve, reject) => {
      const utterance = new SpeechSynthesisUtterance(chunk); utterance.voice = voice;
      const timer = setTimeout(() => { clean(); synthesis.cancel(); reject(new Error("Speech playback timed out.")); }, 45_000);
      function clean() { clearTimeout(timer); signal.removeEventListener("abort", abort); }
      function abort() { clean(); synthesis.cancel(); reject(signal.reason); }
      utterance.onend = () => { clean(); resolve(); };
      utterance.onerror = () => { clean(); reject(new Error("Speech playback failed.")); };
      signal.addEventListener("abort", abort, { once: true });
      synthesis.speak(utterance);
      if (signal.aborted) abort();
    });
  }
}
