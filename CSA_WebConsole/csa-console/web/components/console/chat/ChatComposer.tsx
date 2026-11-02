"use client";

import { useCallback, useEffect, useId, useLayoutEffect, useRef, type ReactNode, type RefObject } from "react";
import { ArrowUp, Mic, VolumeX } from "lucide-react";

const iconButton =
  "inline-grid size-10 shrink-0 place-items-center rounded-[var(--fs-radius-round)] text-[color:var(--fs-text-secondary)] transition-colors duration-150 hover:bg-[var(--fs-surface-blue)] hover:text-[color:var(--fs-text)] focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--fs-focus)] disabled:cursor-not-allowed disabled:text-[color:var(--fs-disabled-text)] disabled:hover:bg-transparent pointer-coarse:size-11";
const sendLook =
  "inline-flex h-10 min-w-10 shrink-0 items-center justify-center gap-2 rounded-[var(--fs-radius-round)] bg-[var(--fs-primary)] text-sm font-medium text-[color:var(--fs-on-primary)] transition-colors duration-150 hover:bg-[var(--fs-primary-hover)] focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--fs-focus)] active:scale-[0.97] disabled:cursor-not-allowed disabled:bg-[var(--fs-disabled-bg)] disabled:text-[color:var(--fs-disabled-text)] disabled:active:scale-100 pointer-coarse:h-11 pointer-coarse:min-w-11";

/** What the first control in the tray can be: anything the reader can operate. */
const TRAY_CONTROLS = "button:not(:disabled), select:not(:disabled), input:not(:disabled)";

/**
 * The composer: a large rounded text box that grows to about eight lines, one microphone button
 * that opens the voice tray (closed by default; dictation, hands-free voice and recorded voice
 * all live inside it), "Stop reading aloud" (only while speech is playing) and a round send
 * button. Enter sends and Shift+Enter starts a new line with a keyboard; on a touch screen Enter
 * is a new line. The send button is named "Send message" (or reads "Send the same message again").
 * Focus follows the tray: opening it with the microphone button moves focus to its first control,
 * closing it returns focus to the button; "Stop reading aloud" hands focus to the microphone
 * button when it is pressed (it unmounts once speech stops), or to a Read aloud button when the microphone
 * and the text box are both off.
 */
export function ChatComposer({ value, onChange, textareaRef, disabled, onSend, sendDisabled, retry,
  speaking, onStopSpeech, voiceOpen, onVoiceToggle, voiceToggleDisabled, voice }: {
  value: string;
  onChange: (value: string) => void;
  textareaRef: RefObject<HTMLTextAreaElement | null>;
  disabled: boolean;
  onSend: () => void;
  sendDisabled: boolean;
  /** An uncertain text send is retained: the button retries the exact request. */
  retry: boolean;
  /** Speech is playing (a reply read aloud or a hands-free reply): "Stop reading aloud" shows. */
  speaking: boolean;
  onStopSpeech: () => void;
  voiceOpen: boolean;
  onVoiceToggle: () => void;
  voiceToggleDisabled: boolean;
  /** The voice tray: dictation, hands-free voice and recorded voice, with their consent and fine print. */
  voice: ReactNode;
}) {
  const trayId = useId();
  const micRef = useRef<HTMLButtonElement>(null);
  const trayRef = useRef<HTMLDivElement>(null);
  // The microphone button just toggled the tray (as opposed to a voice session forcing it open).
  const toggledByButton = useRef(false);
  const wasOpen = useRef(voiceOpen);
  useLayoutEffect(() => {
    const before = wasOpen.current;
    wasOpen.current = voiceOpen;
    if (before === voiceOpen) return;
    const tray = trayRef.current;
    if (voiceOpen) {
      if (toggledByButton.current) tray?.querySelector<HTMLElement>(TRAY_CONTROLS)?.focus();
    } else if (toggledByButton.current || tray?.contains(document.activeElement)) {
      micRef.current?.focus();
    }
    toggledByButton.current = false;
  }, [voiceOpen]);
  // The box is one line tall and grows with its text; past about eight lines it scrolls instead.
  const fit = useCallback(() => {
    const box = textareaRef.current;
    if (!box) return;
    box.style.height = "auto";
    box.style.height = `${box.scrollHeight}px`;
  }, [textareaRef]);
  useLayoutEffect(fit, [fit, value]);
  useEffect(() => {
    window.addEventListener("resize", fit);
    return () => window.removeEventListener("resize", fit);
  }, [fit]);
  return <div className="space-y-2">
    <div ref={trayRef} id={trayId} hidden={!voiceOpen} aria-label="Voice controls" role="group"
      className="max-h-[40dvh] overflow-y-auto rounded-[var(--fs-radius-tile)] border border-[color:var(--fs-border)] bg-[var(--fs-surface)] p-3 text-sm text-[color:var(--fs-text)]">
      {voice}
    </div>
    <div className="rounded-[var(--fs-radius-composer)] border border-[color:var(--fs-border-control)] bg-[var(--fs-surface)] shadow-[var(--fs-shadow-card)] transition-[border-color,box-shadow] duration-150 has-[textarea:focus-visible]:border-[color:var(--fs-primary)] has-[textarea:focus-visible]:outline-2 has-[textarea:focus-visible]:outline-offset-2 has-[textarea:focus-visible]:outline-[color:var(--fs-focus)] has-[textarea:disabled]:bg-[var(--fs-disabled-bg)]">
      <label htmlFor="sandbox-text" className="sr-only">Your question (up to 4000 characters)</label>
      <textarea id="sandbox-text" ref={textareaRef} rows={1} maxLength={4000} value={value} disabled={disabled}
        placeholder="Ask about your knowledge base or products" onChange={(event) => onChange(event.target.value)}
        onKeyDown={(event) => {
          // An IME composition commits with Enter: Chromium and Firefox flag it, Safari sends key code 229.
          if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing || event.keyCode === 229) return;
          if (window.matchMedia("(pointer: coarse)").matches) return;
          event.preventDefault();
          if (!sendDisabled) onSend();
        }}
        className="block max-h-[calc(8lh+1.125rem)] min-h-[3.25rem] w-full resize-none overflow-y-auto bg-transparent px-5 pt-3.5 pb-1 text-base leading-6 text-[color:var(--fs-text)] outline-none focus-visible:shadow-none focus-visible:outline-none! placeholder:text-[color:var(--fs-text-secondary)] disabled:cursor-not-allowed disabled:text-[color:var(--fs-disabled-text)]" />
      <div className="flex items-center gap-1 px-2.5 pb-2.5">
        <button ref={micRef} type="button" aria-expanded={voiceOpen} aria-controls={trayId} disabled={voiceToggleDisabled}
          onClick={() => { toggledByButton.current = true; onVoiceToggle(); }}
          aria-label="Voice options" title="Voice options"
          className={`${iconButton} ${voiceOpen ? "bg-[var(--fs-surface-blue)] text-[color:var(--fs-primary-hover)]" : ""}`}>
          <Mic size={18} aria-hidden /></button>
        {speaking && <button type="button" className={iconButton}
          onClick={() => {
            onStopSpeech();
            // This button goes away once speech stops: keep the reader's place. With the microphone and
            // the text box both off (read-only), fall back to a reply's Read aloud button, then the log.
            const open = (element: HTMLElement | null | undefined) => (element && !(element as HTMLButtonElement).disabled ? element : null);
            const next = open(micRef.current) ?? open(textareaRef.current)
              ?? open(document.querySelector<HTMLElement>('button[aria-label="Read reply aloud"]:not(:disabled)'))
              ?? document.querySelector<HTMLElement>('[data-testid="sandbox-scroll"]');
            next?.focus();
          }}
          aria-label="Stop reading aloud" title="Stop reading aloud"><VolumeX size={18} aria-hidden /></button>}
        <span className="flex-1" />
        <button type="button" className={`${sendLook} ${retry ? "px-4" : ""}`} disabled={sendDisabled} onClick={onSend}
          aria-label={retry ? undefined : "Send message"} title={retry ? undefined : "Send message"}>
          {retry ? "Send the same message again" : <ArrowUp size={18} strokeWidth={2.5} aria-hidden />}</button>
      </div>
    </div>
  </div>;
}
