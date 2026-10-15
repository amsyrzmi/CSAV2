"use client";

import React from "react";

type MarkdownViewerProps = {
  content: string;
  onNavigateNote?: (noteTitle: string) => void;
  className?: string;
};

/**
 * Safe, sanitized Markdown renderer for Obsidian-style notes.
 * Disables raw HTML/scripts and transforms [[WikiLinks]] into interactive links.
 */
export function MarkdownViewer({
  content,
  onNavigateNote,
  className = "",
}: MarkdownViewerProps) {
  if (!content) {
    return (
      <div className="text-sm text-slate-400 italic py-4">
        This note is empty. Click edit to add content.
      </div>
    );
  }

  // Pre-process markdown to render lines
  const lines = content.split("\n");

  const renderInline = (text: string): React.ReactNode[] => {
    // Replace [[Target]] or [[Target|Label]] with interactive link buttons
    const parts: React.ReactNode[] = [];
    const wikiRegex = /\[\[([^\]|]+)(?:\|([^\]]+))?\]\]/g;
    let lastIndex = 0;
    let match: RegExpExecArray | null;

    while ((match = wikiRegex.exec(text)) !== null) {
      const target = match[1].trim();
      const label = match[2]?.trim() || target;
      const matchIndex = match.index;

      if (matchIndex > lastIndex) {
        parts.push(renderBasicInline(text.substring(lastIndex, matchIndex)));
      }

      parts.push(
        <button
          key={`wiki-${matchIndex}`}
          type="button"
          onClick={() => onNavigateNote?.(target)}
          className="inline-flex items-center px-1.5 py-0.5 mx-0.5 rounded text-xs font-medium bg-blue-50 text-blue-700 hover:bg-blue-100 border border-blue-200 transition-colors"
          title={`Open note: ${target}`}
        >
          📄 {label}
        </button>
      );

      lastIndex = matchIndex + match[0].length;
    }

    if (lastIndex < text.length) {
      parts.push(renderBasicInline(text.substring(lastIndex)));
    }

    return parts.length > 0 ? parts : [renderBasicInline(text)];
  };

  const renderBasicInline = (text: string): React.ReactNode => {
    // Escape unsafe tags and parse basic markdown: bold, italic, code
    const safeText = text.replace(/</g, "&lt;").replace(/>/g, "&gt;");
    
    // We can parse `code`, **bold**, *italic* safely using regex splitting
    const codeRegex = /`([^`]+)`/g;
    const segments: React.ReactNode[] = [];
    let lastIdx = 0;
    let codeMatch: RegExpExecArray | null;

    while ((codeMatch = codeRegex.exec(safeText)) !== null) {
      if (codeMatch.index > lastIdx) {
        segments.push(parseEmphasis(safeText.substring(lastIdx, codeMatch.index)));
      }
      segments.push(
        <code
          key={`code-${codeMatch.index}`}
          className="px-1.5 py-0.5 rounded bg-slate-100 text-slate-800 font-mono text-xs border border-slate-200"
        >
          {codeMatch[1]}
        </code>
      );
      lastIdx = codeMatch.index + codeMatch[0].length;
    }

    if (lastIdx < safeText.length) {
      segments.push(parseEmphasis(safeText.substring(lastIdx)));
    }

    return <React.Fragment key={text}>{segments}</React.Fragment>;
  };

  const parseEmphasis = (text: string): React.ReactNode => {
    // Parse **bold** and *italic*
    const parts = text.split(/(\*\*[^*]+\*\*|\*[^*]+\*)/g);
    return parts.map((part, i) => {
      if (part.startsWith("**") && part.endsWith("**")) {
        return <strong key={i}>{part.slice(2, -2)}</strong>;
      }
      if (part.startsWith("*") && part.endsWith("*")) {
        return <em key={i}>{part.slice(1, -1)}</em>;
      }
      return part;
    });
  };

  const elements: React.ReactNode[] = [];
  let inCodeBlock = false;
  let codeBlockBuffer: string[] = [];
  let codeLang = "";

  lines.forEach((line, idx) => {
    // Code block check
    if (line.trim().startsWith("```")) {
      if (inCodeBlock) {
        // End code block
        elements.push(
          <div key={`code-block-${idx}`} className="my-3 rounded-lg overflow-hidden border border-slate-800 bg-slate-900 text-slate-100 font-mono text-xs">
            {codeLang && (
              <div className="px-3 py-1 bg-slate-800 text-slate-400 text-[10px] font-semibold uppercase tracking-wider border-b border-slate-700">
                {codeLang}
              </div>
            )}
            <pre className="p-3 overflow-x-auto whitespace-pre leading-relaxed">
              <code>{codeBlockBuffer.join("\n")}</code>
            </pre>
          </div>
        );
        inCodeBlock = false;
        codeBlockBuffer = [];
        codeLang = "";
      } else {
        inCodeBlock = true;
        codeLang = line.trim().slice(3).trim();
      }
      return;
    }

    if (inCodeBlock) {
      codeBlockBuffer.push(line);
      return;
    }

    const trimmed = line.trim();

    // Headings
    if (trimmed.startsWith("# ")) {
      elements.push(
        <h1 key={idx} className="text-2xl font-bold text-slate-900 mt-6 mb-3 pb-2 border-b border-slate-200">
          {renderInline(trimmed.slice(2))}
        </h1>
      );
    } else if (trimmed.startsWith("## ")) {
      elements.push(
        <h2 key={idx} className="text-xl font-bold text-slate-900 mt-5 mb-2.5 pb-1 border-b border-slate-100">
          {renderInline(trimmed.slice(3))}
        </h2>
      );
    } else if (trimmed.startsWith("### ")) {
      elements.push(
        <h3 key={idx} className="text-base font-semibold text-slate-800 mt-4 mb-2">
          {renderInline(trimmed.slice(4))}
        </h3>
      );
    } else if (trimmed.startsWith("#### ")) {
      elements.push(
        <h4 key={idx} className="text-sm font-semibold text-slate-700 mt-3 mb-1">
          {renderInline(trimmed.slice(5))}
        </h4>
      );
    } else if (trimmed.startsWith("> ")) {
      // Blockquote
      elements.push(
        <blockquote key={idx} className="pl-4 py-1 my-2 border-l-4 border-blue-400 bg-blue-50/40 text-slate-700 text-sm italic rounded-r">
          {renderInline(trimmed.slice(2))}
        </blockquote>
      );
    } else if (trimmed.startsWith("- [ ] ") || trimmed.startsWith("* [ ] ")) {
      // Checklist item unchecked
      elements.push(
        <div key={idx} className="flex items-center gap-2 my-1 text-sm text-slate-700">
          <input type="checkbox" disabled checked={false} className="rounded border-slate-300 text-blue-600" />
          <span>{renderInline(trimmed.slice(6))}</span>
        </div>
      );
    } else if (trimmed.startsWith("- [x] ") || trimmed.startsWith("* [x] ") || trimmed.startsWith("- [X] ")) {
      // Checklist item checked
      elements.push(
        <div key={idx} className="flex items-center gap-2 my-1 text-sm text-slate-400 line-through">
          <input type="checkbox" disabled checked={true} className="rounded border-slate-300 text-blue-600" />
          <span>{renderInline(trimmed.slice(6))}</span>
        </div>
      );
    } else if (trimmed.startsWith("- ") || trimmed.startsWith("* ")) {
      // Bullet list
      elements.push(
        <div key={idx} className="flex items-start gap-2 my-1 ml-2 text-sm text-slate-700">
          <span className="text-slate-400 mt-1 leading-none">•</span>
          <div className="flex-1">{renderInline(trimmed.slice(2))}</div>
        </div>
      );
    } else if (/^\d+\.\s/.test(trimmed)) {
      // Numbered list
      const numMatch = trimmed.match(/^(\d+)\.\s(.*)$/);
      if (numMatch) {
        elements.push(
          <div key={idx} className="flex items-start gap-2 my-1 ml-2 text-sm text-slate-700">
            <span className="text-slate-400 font-mono text-xs mt-0.5">{numMatch[1]}.</span>
            <div className="flex-1">{renderInline(numMatch[2])}</div>
          </div>
        );
      }
    } else if (trimmed === "---" || trimmed === "***") {
      // Divider
      elements.push(<hr key={idx} className="my-4 border-slate-200" />);
    } else if (!trimmed) {
      // Empty line / paragraph break
      elements.push(<div key={idx} className="h-2" />);
    } else {
      // Regular paragraph
      elements.push(
        <p key={idx} className="text-sm text-slate-700 leading-relaxed my-1.5">
          {renderInline(trimmed)}
        </p>
      );
    }
  });

  return (
    <div className={`prose-sm max-w-none text-slate-800 ${className}`}>
      {elements}
    </div>
  );
}
