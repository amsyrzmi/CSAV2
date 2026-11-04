"use client";

import { useEffect, useState } from "react";

/**
 * Whether the browser is speaking right now, from any source (a reply read aloud or a hands-free
 * reply). The speech engine raises no event for a page that did not create the utterance, so this
 * asks it a few times a second; it asks nothing while the tab is hidden and does not re-render
 * unless the answer changes.
 */
export function useSpeaking(): boolean {
  const [speaking, setSpeaking] = useState(false);
  useEffect(() => {
    const synthesizer = window.speechSynthesis;
    if (!synthesizer) return;
    const read = () => { if (!document.hidden) setSpeaking(synthesizer.speaking); };
    const timer = setInterval(read, 300);
    return () => clearInterval(timer);
  }, []);
  return speaking;
}
