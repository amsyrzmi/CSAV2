"use client";

import { useId, type ReactNode, type RefObject } from "react";
import { ArrowUp, AudioLines, Mic, MicOff, VolumeX } from "lucide-react";

const iconButton =
  "inline-grid size-10 shrink-0 place-items-center rounded-full text-[color:var(--fs-text-secondary)] transition-colors duration-150 hover:bg-[var(--fs-surface-blue)] hover:text-[color:var(--fs-text)] focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--fs-focus)] disabled:cursor-not-allowed disabled:text-[color:var(--fs-disabled-text)] disabled:hover:bg-transparent pointer-coarse:size-11";
const sendLook =
  "inline-flex h-10 min-w-10 shrink-0 items-center justify-center gap-2 rounded-full bg-[var(--fs-primary)] text-sm font-medium text-[color:var(--fs-on-primary)] transition-colors duration-150 hover:bg-[var(--fs-primary-hover)] focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--fs-focus)] active:scale-[0.97] disabled:cursor-not-allowed disabled:bg-[var(--fs-disabled-bg)] disabled:text-[color:var(--fs-disabled-text)] disabled:active:scale-100 pointer-coarse:h-11 pointer-coarse:min-w-11";

/**
 * The composer: a large rounded text box that grows to about eight lines, the microphone
 * (local dictation), stop speech, the voice options tray and a round send button. Enter sends
 * and Shift+Enter starts a new line with a keyboard; on a touch screen Enter is a new line.
 * The send button keeps the name "Send to sandbox" (or reads "Retry exact request").
 */
export function ChatComposer({ value, onChange, textareaRef, disabled, onSend, sendDisabled, retry,
  listening, onDictate, dictateDisabled, onStopSpeech, voiceOpen, onVoiceToggle, voiceToggleDisabled, voice }: {
  value: string;
  onChange: (value: string) => void;
  textareaRef: RefObject<HTMLTextAreaElement | null>;
  disabled: boolean;
  onSend: () => void;
  sendDisabled: boolean;
  /** An uncertain text send is retained: the button retries the exact request. */
  retry: boolean;
  listening: boolean;
  onDictate: () => void;
  dictateDisabled: boolean;
  onStopSpeech: () => void;
  voiceOpen: boolean;
  onVoiceToggle: () => void;
  voiceToggleDisabled: boolean;
  /** The voice options tray: dictation language, hands-free voice and recorded voice. */
  voice: ReactNode;
}) {
  const trayId = useId();
  return <div className="space-y-2">
    <div id={trayId} hidden={!voiceOpen} aria-label="Voice options" role="group"
      className="max-h-[40dvh] overflow-y-auto rounded-2xl border border-[color:var(--fs-border)] bg-[var(--fs-surface)] p-3 text-sm text-[color:var(--fs-text)]">
      {voice}
    </div>
    <div className="rounded-[1.75rem] border border-[color:var(--fs-border-control)] bg-[var(--fs-surface)] shadow-[var(--fs-shadow-card)] transition-[border-color,box-shadow] duration-150 has-[textarea:focus-visible]:border-[color:var(--fs-primary)] has-[textarea:focus-visible]:outline-2 has-[textarea:focus-visible]:outline-offset-2 has-[textarea:focus-visible]:outline-[color:var(--fs-focus)] has-[textarea:disabled]:bg-[var(--fs-disabled-bg)]">
      <label htmlFor="sandbox-text" className="sr-only">Text question (maximum 4000 characters)</label>
      <textarea id="sandbox-text" ref={textareaRef} rows={1} maxLength={4000} value={value} disabled={disabled}
        placeholder="Ask a knowledge or product question" onChange={(event) => onChange(event.target.value)}
        onKeyDown={(event) => {
          if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing) return;
          if (window.matchMedia("(pointer: coarse)").matches) return;
          event.preventDefault();
          if (!sendDisabled) onSend();
        }}
        className="block max-h-[13.5rem] min-h-[3.25rem] w-full resize-none bg-transparent px-5 pt-3.5 pb-1 text-base leading-6 text-[color:var(--fs-text)] outline-none [field-sizing:content] placeholder:text-[color:var(--fs-text-secondary)] disabled:cursor-not-allowed disabled:text-[color:var(--fs-disabled-text)]" />
      <div className="flex items-center gap-1 px-2.5 pb-2.5">
        <button type="button" className={iconButton} disabled={dictateDisabled} onClick={onDictate}
          aria-label={listening ? "Stop microphone" : "Dictate locally"} title={listening ? "Stop microphone" : "Dictate locally"}>
          {listening ? <MicOff size={18} aria-hidden /> : <Mic size={18} aria-hidden />}</button>
        <button type="button" className={iconButton} onClick={onStopSpeech} aria-label="Stop speech" title="Stop speech">
          <VolumeX size={18} aria-hidden /></button>
        <button type="button" aria-expanded={voiceOpen} aria-controls={trayId} disabled={voiceToggleDisabled} onClick={onVoiceToggle}
          aria-label="Voice options"
          className={`inline-flex h-10 items-center gap-1.5 rounded-full px-3 text-sm transition-colors duration-150 hover:bg-[var(--fs-surface-blue)] focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--fs-focus)] disabled:cursor-not-allowed pointer-coarse:h-11 ${voiceOpen
            ? "bg-[var(--fs-surface-blue)] text-[color:var(--fs-primary-hover)]" : "text-[color:var(--fs-text-secondary)] hover:text-[color:var(--fs-text)]"}`}>
          <AudioLines size={18} aria-hidden /><span className="max-[359px]:sr-only">Voice</span></button>
        <span className="flex-1" />
        <button type="button" className={`${sendLook} ${retry ? "px-4" : ""}`} disabled={sendDisabled} onClick={onSend}
          aria-label={retry ? undefined : "Send to sandbox"} title={retry ? undefined : "Send to sandbox"}>
          {retry ? "Retry exact request" : <ArrowUp size={18} strokeWidth={2.5} aria-hidden />}</button>
      </div>
    </div>
  </div>;
}
