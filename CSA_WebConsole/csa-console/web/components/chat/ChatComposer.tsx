import React, { useRef, useState, useEffect } from "react";
import { ArrowUp, Mic, X, Square } from "lucide-react";
import clsx from "clsx";

interface ChatComposerProps {
  onSend: (text: string, files: File[]) => void;
  onVoiceSubmit: (blob: Blob) => void;
  disabled?: boolean;
}

export function ChatComposer({ onSend, onVoiceSubmit, disabled }: ChatComposerProps) {
  const [text, setText] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [isRecording, setIsRecording] = useState(false);
  const [recordingTime, setRecordingTime] = useState(0);
  const [error, setError] = useState("");
  
  const fileInputRef = useRef<HTMLInputElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioChunksRef = useRef<BlobPart[]>([]);
  const timerRef = useRef<NodeJS.Timeout | null>(null);
  const isCancelledRef = useRef<boolean>(false);

  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
      textareaRef.current.style.height = `${Math.min(textareaRef.current.scrollHeight, 220)}px`;
    }
  }, [text]);

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setError("");
    const selected = Array.from(e.target.files || []);
    if (files.length + selected.length > 5) {
      setError("At most 5 files.");
      return;
    }
    const valid = selected.filter(f => {
      const ext = f.name.toLowerCase();
      if (!ext.endsWith(".pdf") && !ext.endsWith(".docx")) {
        setError("Only PDF and DOCX files are supported.");
        return false;
      }
      if (f.size > 10 * 1024 * 1024) {
        setError("File too large (10 MB each).");
        return false;
      }
      return true;
    });
    setFiles([...files, ...valid]);
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  const handleRemoveFile = (index: number) => {
    setFiles(files.filter((_, i) => i !== index));
  };

  const handleSend = () => {
    if ((!text.trim() && files.length === 0) || disabled) return;
    onSend(text.trim(), files);
    setText("");
    setFiles([]);
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      handleSend();
    }
  };

  const startRecording = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      let mime = "audio/webm;codecs=opus";
      if (!MediaRecorder.isTypeSupported(mime)) {
        mime = "audio/webm";
        if (!MediaRecorder.isTypeSupported(mime)) {
          mime = "audio/mp4"; // Safari fallback
        }
      }
      const recorder = new MediaRecorder(stream, { mimeType: mime });
      audioChunksRef.current = [];
      
      recorder.ondataavailable = e => {
        if (e.data.size > 0) audioChunksRef.current.push(e.data);
      };
      
      isCancelledRef.current = false;
      
      recorder.onstop = () => {
        const blob = new Blob(audioChunksRef.current, { type: mime });
        stream.getTracks().forEach(t => t.stop());
        if (!isCancelledRef.current) { // If not cancelled
          onVoiceSubmit(blob);
        }
      };
      
      mediaRecorderRef.current = recorder;
      recorder.start();
      setIsRecording(true);
      setRecordingTime(0);
      
      timerRef.current = setInterval(() => {
        setRecordingTime(t => {
          if (t >= 119) {
            stopRecording();
            return 120;
          }
          return t + 1;
        });
      }, 1000);
      
    } catch (_) {
      setError("Microphone access denied or unavailable.");
    }
  };

  const stopRecording = () => {
    if (timerRef.current) clearInterval(timerRef.current);
    if (mediaRecorderRef.current && mediaRecorderRef.current.state === "recording") {
      mediaRecorderRef.current.stop();
    }
    setIsRecording(false);
  };

  const cancelRecording = () => {
    setIsRecording(false);
    isCancelledRef.current = true;
    if (timerRef.current) clearInterval(timerRef.current);
    if (mediaRecorderRef.current && mediaRecorderRef.current.state === "recording") {
      mediaRecorderRef.current.stream.getTracks().forEach(t => t.stop());
      mediaRecorderRef.current.stop(); // This triggers onstop but since isCancelledRef is true it won't submit
    }
  };

  return (
    <div className="flex flex-col gap-2">
      {error && <div className="text-red-500 text-xs px-2">{error}</div>}
      
      <div className="rounded-2xl border border-ink-200 bg-white p-2 pl-4 shadow-[var(--shadow-sm)] focus-within:border-brand-500 focus-within:ring-[3px] focus-within:ring-brand-500/15">
        
        {files.length > 0 && (
          <div className="flex flex-wrap gap-2 mb-2">
            {files.map((f, i) => (
              <div key={i} className="flex items-center gap-1.5 bg-ink-100/50 border border-ink-200 text-ink-700 px-2 py-1 rounded-lg text-xs">
                <span className="truncate max-w-[120px]">{f.name}</span>
                <button type="button" onClick={() => handleRemoveFile(i)} className="text-ink-400 hover:text-ink-700">
                  <X size={12} />
                </button>
              </div>
            ))}
          </div>
        )}

        {isRecording ? (
          <div className="flex items-center gap-4 py-2 px-2">
            <div className="flex items-center gap-2 flex-1">
              <span className="h-2 w-2 rounded-full bg-red-500 animate-pulse" />
              <span className="text-sm font-medium">{Math.floor(recordingTime / 60)}:{(recordingTime % 60).toString().padStart(2, "0")}</span>
            </div>
            <button type="button" onClick={cancelRecording} className="text-ink-400 hover:text-ink-700 p-2">
              <X size={18} />
            </button>
            <button type="button" onClick={stopRecording} className="flex h-9 w-9 items-center justify-center rounded-xl bg-brand-600 text-white shadow-sm hover:bg-brand-700">
              <Square size={14} fill="currentColor" />
            </button>
          </div>
        ) : (
          <div className="flex items-end gap-2">

            <textarea
              ref={textareaRef}
              value={text}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Message your assistant…"
              maxLength={4000}
              className="max-h-[220px] min-h-[36px] flex-1 resize-none bg-transparent py-2 text-[13.5px] leading-5 text-ink-900 outline-none placeholder:text-ink-400"
            />
            {text.trim() || files.length > 0 ? (
              <button
                type="button"
                onClick={handleSend}
                disabled={disabled}
                className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-ink-900 text-white shadow-[var(--shadow-sm)] hover:bg-ink-800 disabled:bg-ink-200 disabled:text-ink-400"
              >
                <ArrowUp size={16} strokeWidth={2.4} />
              </button>
            ) : (
              <button
                type="button"
                onClick={startRecording}
                disabled={disabled}
                className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-ink-100 text-ink-600 hover:bg-ink-200"
              >
                <Mic size={16} />
              </button>
            )}
          </div>
        )}
      </div>
      <div className="mt-1 flex justify-between px-1 text-[11px] text-ink-400">
        <span><span className="kbd">Enter</span> to send · <span className="kbd">Shift</span> + <span className="kbd">Enter</span> for new line</span>
        <span className={clsx("num", text.length > 3600 && "text-amber-600")}>{text.length}/4000</span>
      </div>
    </div>
  );
}
