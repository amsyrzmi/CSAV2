"use client";

import React, { useEffect, useRef, useState } from "react";
import { AlertCircle, Loader2 } from "lucide-react";
import { Banner } from "@/components/ui/kit";

type Props = {
  source: string;
  onSvgRendered?: (svg: string) => void;
  className?: string;
};

export function MermaidViewer({ source, onSvgRendered, className }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const cleanSource = source?.trim() || "";

  const [renderedSvg, setRenderedSvg] = useState<string>("");
  const [renderedHash, setRenderedHash] = useState<string>("");
  const [error, setError] = useState<string | null>(null);

  const isRendering = Boolean(cleanSource) && renderedHash !== cleanSource && !error;

  useEffect(() => {
    if (!cleanSource) {
      return;
    }

    let isMounted = true;

    const renderDiagram = async () => {
      try {
        const mermaid = (await import("mermaid")).default;
        mermaid.initialize({
          startOnLoad: false,
          securityLevel: "strict",
          theme: "neutral",
          fontFamily: "inherit",
          logLevel: "error",
        });

        const uniqueId = `mermaid-${Math.random().toString(36).substring(2, 9)}`;
        const { svg } = await mermaid.render(uniqueId, cleanSource);

        if (isMounted) {
          setRenderedSvg(svg);
          setRenderedHash(cleanSource);
          setError(null);
          if (onSvgRendered) {
            onSvgRendered(svg);
          }
        }
      } catch (err: unknown) {
        if (isMounted) {
          const msg = err instanceof Error ? err.message : String(err);
          // Strip any DOM elements mermaid may have appended to body on failure
          const badEls = document.querySelectorAll(`[id^="dmermaid-"], [id^="mermaid-"]`);
          badEls.forEach((el) => {
            if (el.parentNode && el !== containerRef.current) {
              el.parentNode.removeChild(el);
            }
          });
          setError(msg || "Syntax error rendering Mermaid diagram");
          setRenderedHash(cleanSource);
        }
      }
    };

    renderDiagram();

    return () => {
      isMounted = false;
    };
  }, [cleanSource, onSvgRendered]);

  if (!cleanSource) {
    return (
      <div className={`relative flex items-center justify-center p-8 text-xs text-slate-400 italic ${className || ""}`}>
        No diagram source provided
      </div>
    );
  }

  return (
    <div className={`relative flex flex-col h-full w-full ${className || ""}`}>
      {isRendering && (
        <div className="absolute inset-0 z-10 flex items-center justify-center bg-white/70 backdrop-blur-xs">
          <div className="flex items-center gap-2 text-xs text-slate-500 font-medium">
            <Loader2 className="w-4 h-4 animate-spin text-blue-600" />
            Rendering diagram...
          </div>
        </div>
      )}

      {error ? (
        <div className="p-4 space-y-3">
          <Banner tone="error">
            <div className="space-y-1">
              <strong className="block text-xs font-semibold">Diagram Syntax Error:</strong>
              <pre className="text-[11px] font-mono whitespace-pre-wrap max-h-48 overflow-y-auto opacity-90">
                {error}
              </pre>
            </div>
          </Banner>
          <div className="text-xs text-slate-500 bg-slate-50 p-3 rounded-lg border border-slate-100 flex items-center gap-2">
            <AlertCircle className="w-4 h-4 text-amber-500 shrink-0" />
            <span>Verify diagram keywords, indentation, and closed brackets in the source editor.</span>
          </div>
        </div>
      ) : (
        <div
          ref={containerRef}
          className="flex-1 w-full overflow-auto p-6 flex items-center justify-center bg-white rounded-xl select-none"
          dangerouslySetInnerHTML={{ __html: renderedSvg }}
        />
      )}
    </div>
  );
}
