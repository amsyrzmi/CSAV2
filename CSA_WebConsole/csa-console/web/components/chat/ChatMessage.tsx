import clsx from "clsx";
import { Bot, Copy, RefreshCcw, File as FileIcon, CheckCircle2 } from "lucide-react";
import DOMPurify from "dompurify";
import { marked } from "marked";
import { Message } from "../../lib/chat-types";
import { useState } from "react";

export function ChatMessage({ message, onRegenerate }: { message: Message; onRegenerate?: () => void }) {
  const isUser = message.role === "user";
  const [copied, setCopied] = useState(false);

  const copyText = () => {
    navigator.clipboard.writeText(message.text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  if (message.role === "system" || message.status === "held") {
    return (
      <div className="flex justify-center my-4">
        <div className="text-xs bg-ink-100 text-ink-600 px-3 py-1.5 rounded-full border border-ink-200">
          {message.text || "A team member will review this and follow up"}
        </div>
      </div>
    );
  }

  // Render HTML safely
  const html = !isUser && message.text ? DOMPurify.sanitize(marked.parse(message.text) as string) : "";

  return (
    <div className={clsx("flex gap-3 animate-fade-up group", isUser && "justify-end")}>
      {!isUser && (
        <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-ink-900 text-white">
          <Bot size={14} />
        </span>
      )}
      <div className={clsx("max-w-[85%] flex flex-col gap-2")}>
        {message.files && message.files.length > 0 && (
          <div className="flex flex-wrap gap-2">
            {message.files.map((f, i) => (
              <div key={i} className="flex items-center gap-1.5 bg-ink-100/50 border border-ink-200 text-ink-700 px-2.5 py-1.5 rounded-lg text-[12.5px]">
                <FileIcon size={14} />
                <span className="truncate max-w-[150px]">{f.name}</span>
                {!isUser && <CheckCircle2 size={14} className="text-brand-600" />}
              </div>
            ))}
          </div>
        )}

        {(message.text || message.status === "transcribing" || message.status === "processing") && (
          <div
            className={clsx(
              "whitespace-pre-wrap text-[13.5px] leading-6",
              isUser
                ? "rounded-2xl rounded-br-md bg-ink-900 px-4 py-2.5 text-white shadow-[var(--shadow-sm)]"
                : "pt-0.5 text-ink-800"
            )}
          >
            {isUser ? (
              message.text || (message.status === "transcribing" ? <span className="animate-pulse">Transcribing...</span> : "")
            ) : message.status === "processing" ? (
              <div className="flex items-center gap-1 mt-1">
                <span className="typing-dot" />
                <span className="typing-dot" />
                <span className="typing-dot" />
              </div>
            ) : (
              <div className="prose prose-sm max-w-none" dangerouslySetInnerHTML={{ __html: html }} />
            )}
          </div>
        )}

        {!isUser && message.status === "done" && (
          <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity mt-1">
            <button onClick={copyText} className="p-1.5 text-ink-400 hover:text-ink-700 hover:bg-ink-100 rounded-md">
              <Copy size={13} />
            </button>
            {onRegenerate && (
              <button onClick={onRegenerate} className="p-1.5 text-ink-400 hover:text-ink-700 hover:bg-ink-100 rounded-md">
                <RefreshCcw size={13} />
              </button>
            )}
            {copied && <span className="text-[10px] text-ink-500">Copied!</span>}
          </div>
        )}
      </div>
    </div>
  );
}
