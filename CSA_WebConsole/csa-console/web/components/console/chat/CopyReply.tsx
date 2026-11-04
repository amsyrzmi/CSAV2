"use client";

import { useEffect, useRef, useState } from "react";
import { Check, Copy } from "lucide-react";

/**
 * Copies one reply's plain text (nothing else from the page) to the clipboard. The button keeps its
 * name; the words "Copied" (or "Could not copy" when the browser refuses) show beside it for two
 * seconds.
 */
export function CopyReply({ body }: { body: string }) {
  const [result, setResult] = useState<"copied" | "failed" | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);
  async function copy() {
    let outcome: "copied" | "failed" = "copied";
    try { await navigator.clipboard.writeText(body); }
    catch { outcome = "failed"; }
    setResult(outcome);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setResult(null), 2000);
  }
  return <>
    <button type="button" aria-label="Copy reply" title="Copy reply" onClick={() => void copy()}
      className="inline-grid size-9 place-items-center rounded-[var(--fs-radius-control)] text-[color:var(--fs-text-secondary)] transition-colors duration-150 hover:bg-[var(--fs-surface-blue)] hover:text-[color:var(--fs-text)] focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--fs-focus)] pointer-coarse:size-11">
      {result === "copied" ? <Check size={16} aria-hidden /> : <Copy size={16} aria-hidden />}</button>
    {result && <span className="text-xs text-[color:var(--fs-text-secondary)]">{result === "copied" ? "Copied" : "Could not copy"}</span>}
  </>;
}
