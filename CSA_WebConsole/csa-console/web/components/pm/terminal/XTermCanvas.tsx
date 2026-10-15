"use client";

import { useEffect, useRef, useState } from "react";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";
import { post } from "@/lib/api";
import { RefreshCw, WifiOff } from "lucide-react";
import { Button } from "@/components/ui/kit";

type XTermCanvasProps = {
  sessionId: string;
  onTerminated?: () => void;
};

type TicketResponse = {
  ticket: string;
  session_id: string;
  expires_in_seconds: number;
};

export function XTermCanvas({ sessionId, onTerminated }: XTermCanvasProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const termRef = useRef<Terminal | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const fitAddonRef = useRef<FitAddon | null>(null);

  const [connecting, setConnecting] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reconnectCount, setReconnectCount] = useState(0);

  useEffect(() => {
    let isCancelled = false;

    if (!containerRef.current) return;

    // Clean up any existing connection
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }
    if (termRef.current) {
      termRef.current.dispose();
      termRef.current = null;
    }

    const initConnection = async () => {
      try {
        // 1. Mint single-use ticket
        const ticketRes = await post<TicketResponse>(
          `/api/admin/pm/terminal-sessions/${sessionId}/ticket`
        );
        if (isCancelled) return;
        const ticket = ticketRes.ticket;

        if (!containerRef.current) return;

        // 2. Initialize xterm.js instance
        const term = new Terminal({
          cursorBlink: true,
          cursorStyle: "bar",
          fontSize: 13,
          lineHeight: 1.25,
          fontFamily: "'JetBrains Mono', 'Fira Code', Menlo, Monaco, 'Courier New', monospace",
          theme: {
            background: "#0A0E17",
            foreground: "#E2E8F0",
            cursor: "#3B82F6",
            cursorAccent: "#0A0E17",
            selectionBackground: "#1E293B",
            selectionForeground: "#F8FAFC",
            black: "#0F172A",
            red: "#EF4444",
            green: "#10B981",
            yellow: "#F59E0B",
            blue: "#3B82F6",
            magenta: "#8B5CF6",
            cyan: "#06B6D4",
            white: "#F8FAFC",
            brightBlack: "#475569",
            brightRed: "#F87171",
            brightGreen: "#34D399",
            brightYellow: "#FBBF24",
            brightBlue: "#60A5FA",
            brightMagenta: "#A78BFA",
            brightCyan: "#22D3EE",
            brightWhite: "#FFFFFF",
          },
        });

        const fitAddon = new FitAddon();
        term.loadAddon(fitAddon);
        term.open(containerRef.current);
        fitAddon.fit();

        termRef.current = term;
        fitAddonRef.current = fitAddon;

        // 3. Form WebSocket endpoint
        const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
        const host = window.location.port === "3001" ? "127.0.0.1:8100" : window.location.host;
        const wsUrl = `${proto}//${host}/api/admin/pm/terminal-sessions/${sessionId}/ws?ticket=${ticket}`;

        const ws = new WebSocket(wsUrl);
        wsRef.current = ws;

        ws.onopen = () => {
          if (isCancelled) return;
          setConnecting(false);
          setError(null);
          try {
            ws.send(JSON.stringify({ type: "resize", cols: term.cols, rows: term.rows }));
          } catch {
            // ignore
          }
        };

        ws.onmessage = (event) => {
          term.write(event.data);
        };

        ws.onerror = () => {
          if (isCancelled) return;
          setError("WebSocket connection failed. Verify runner service is active.");
          setConnecting(false);
        };

        ws.onclose = (event) => {
          if (isCancelled) return;
          setConnecting(false);
          if (event.code === 4400 || event.code === 4404) {
            setError("Terminal session has ended or is invalid.");
            onTerminated?.();
          } else if (event.code !== 1000) {
            setError(event.reason || "Connection closed unexpectedly.");
          }
        };

        term.onData((data) => {
          if (ws.readyState === WebSocket.OPEN) {
            ws.send(data);
          }
        });

      } catch (err: unknown) {
        if (isCancelled) return;
        setError(err instanceof Error ? err.message : "Failed to establish terminal session");
        setConnecting(false);
      }
    };

    initConnection();

    // Resize observer to handle dynamic window / pane resizing
    const resizeObserver = new ResizeObserver(() => {
      if (fitAddonRef.current && termRef.current && wsRef.current?.readyState === WebSocket.OPEN) {
        try {
          fitAddonRef.current.fit();
          wsRef.current.send(
            JSON.stringify({
              type: "resize",
              cols: termRef.current.cols,
              rows: termRef.current.rows,
            })
          );
        } catch {
          // ignore
        }
      }
    });

    if (containerRef.current) {
      resizeObserver.observe(containerRef.current);
    }

    return () => {
      isCancelled = true;
      resizeObserver.disconnect();
      if (wsRef.current) {
        try {
          wsRef.current.send(JSON.stringify({ type: "detach" }));
          wsRef.current.close(1000);
        } catch {
          // ignore
        }
        wsRef.current = null;
      }
      if (termRef.current) {
        termRef.current.dispose();
        termRef.current = null;
      }
    };
  }, [sessionId, reconnectCount, onTerminated]);

  const handleReconnect = () => {
    setConnecting(true);
    setError(null);
    setReconnectCount((c) => c + 1);
  };

  return (
    <div className="relative flex h-full w-full flex-col overflow-hidden rounded-xl border border-ink-800 bg-[#0A0E17] shadow-inner">
      {/* Terminal Canvas Container */}
      <div ref={containerRef} className="h-full w-full p-2.5" />

      {/* Connection & Status Overlay */}
      {connecting && (
        <div className="absolute inset-0 flex items-center justify-center bg-[#0A0E17]/80 backdrop-blur-xs">
          <div className="flex items-center gap-2.5 rounded-xl border border-ink-700 bg-ink-900/90 px-4 py-2.5 text-xs text-ink-300 shadow-lg">
            <RefreshCw size={14} className="animate-spin text-brand-400" />
            <span>Establishing secure PTY channel...</span>
          </div>
        </div>
      )}

      {error && !connecting && (
        <div className="absolute inset-0 flex items-center justify-center bg-[#0A0E17]/85 backdrop-blur-xs p-4">
          <div className="flex max-w-sm flex-col items-center gap-3 rounded-2xl border border-ink-700 bg-ink-900/95 p-6 text-center shadow-xl">
            <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-rose-500/10 text-rose-400">
              <WifiOff size={20} />
            </div>
            <div>
              <h4 className="text-sm font-semibold text-white">Connection Disconnected</h4>
              <p className="mt-1 text-xs text-ink-400">{error}</p>
            </div>
            <Button size="sm" variant="secondary" onClick={handleReconnect} className="mt-2 text-xs">
              <RefreshCw size={12} className="mr-1.5" /> Reconnect Terminal
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
