"use client";

import React, { useMemo } from "react";
import { NoteGraphData, NoteGraphNode } from "../types";

type NeighborhoodGraphProps = {
  data: NoteGraphData;
  activeNoteId?: string;
  onSelectNote: (noteId: string) => void;
  className?: string;
};

export function NeighborhoodGraph({
  data,
  activeNoteId,
  onSelectNote,
  className = "",
}: NeighborhoodGraphProps) {
  const width = 420;
  const height = 320;
  const centerX = width / 2;
  const centerY = height / 2;

  // Calculate layout positions
  const { nodePositions, linksWithCoords } = useMemo(() => {
    const nodes = data.nodes || [];
    const links = data.links || [];

    if (nodes.length === 0) {
      return { nodePositions: [], linksWithCoords: [] };
    }

    const posMap = new Map<string, { x: number; y: number; node: NoteGraphNode; isCenter: boolean }>();

    // Central node
    const centerNode = nodes.find((n) => n.id === activeNoteId) || nodes[0];
    posMap.set(centerNode.id, { x: centerX, y: centerY, node: centerNode, isCenter: true });

    // Other nodes in radial distribution
    const otherNodes = nodes.filter((n) => n.id !== centerNode.id);
    const radius = Math.min(width, height) * 0.36;
    const angleStep = otherNodes.length > 0 ? (2 * Math.PI) / otherNodes.length : 0;

    otherNodes.forEach((node, idx) => {
      const angle = idx * angleStep - Math.PI / 2;
      const x = centerX + radius * Math.cos(angle);
      const y = centerY + radius * Math.sin(angle);
      posMap.set(node.id, { x, y, node, isCenter: false });
    });

    const calculatedLinks = links
      .map((link) => {
        const sourcePos = posMap.get(link.source);
        const targetPos = posMap.get(link.target);
        if (!sourcePos || !targetPos) return null;
        return {
          source: sourcePos,
          target: targetPos,
          label: link.label,
        };
      })
      .filter(Boolean);

    return {
      nodePositions: Array.from(posMap.values()),
      linksWithCoords: calculatedLinks,
    };
  }, [data, activeNoteId, centerX, centerY, width, height]);

  if (!data.nodes || data.nodes.length === 0) {
    return (
      <div className={`flex flex-col items-center justify-center p-6 text-center bg-slate-50 rounded-xl border border-dashed border-slate-200 text-xs text-slate-400 ${className}`}>
        <span className="text-2xl mb-1">🕸️</span>
        <span>No connected notes in graph.</span>
        <span className="text-[11px] text-slate-400 mt-0.5">Use [[WikiLinks]] in your notes to link ideas together.</span>
      </div>
    );
  }

  return (
    <div className={`relative bg-slate-900 rounded-xl overflow-hidden border border-slate-800 shadow-inner ${className}`}>
      {/* Mini header badge */}
      <div className="absolute top-2.5 left-3 z-10 flex items-center gap-1.5">
        <span className="w-2 h-2 rounded-full bg-blue-500 animate-pulse" />
        <span className="text-[11px] font-semibold text-slate-300 tracking-wide uppercase">Neighborhood Graph</span>
      </div>

      <svg width="100%" height={height} viewBox={`0 0 ${width} ${height}`} className="cursor-default select-none">
        <defs>
          <linearGradient id="edgeGradient" x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" stopColor="#3B82F6" stopOpacity="0.4" />
            <stop offset="100%" stopColor="#60A5FA" stopOpacity="0.8" />
          </linearGradient>
          <filter id="glow" x="-20%" y="-20%" width="140%" height="140%">
            <feDropShadow dx="0" dy="0" stdDeviation="3" floodColor="#3B82F6" floodOpacity="0.5" />
          </filter>
        </defs>

        {/* Links */}
        {linksWithCoords.map((link, idx) => {
          if (!link) return null;
          return (
            <g key={`edge-${idx}`}>
              <line
                x1={link.source.x}
                y1={link.source.y}
                x2={link.target.x}
                y2={link.target.y}
                stroke="url(#edgeGradient)"
                strokeWidth="1.5"
                strokeDasharray={link.source.isCenter || link.target.isCenter ? undefined : "3,3"}
              />
            </g>
          );
        })}

        {/* Nodes */}
        {nodePositions.map(({ x, y, node, isCenter }) => {
          const isSelected = isCenter || node.id === activeNoteId;
          const nodeRadius = isSelected ? 22 : 16;

          return (
            <g
              key={node.id}
              transform={`translate(${x}, ${y})`}
              className="cursor-pointer group"
              onClick={() => onSelectNote(node.id)}
            >
              {/* Outer glow ring */}
              {isSelected && (
                <circle
                  r={nodeRadius + 4}
                  fill="none"
                  stroke="#3B82F6"
                  strokeWidth="2"
                  strokeOpacity="0.4"
                  filter="url(#glow)"
                />
              )}

              {/* Node Circle */}
              <circle
                r={nodeRadius}
                fill={isSelected ? "#2563EB" : "#1E293B"}
                stroke={isSelected ? "#60A5FA" : "#475569"}
                strokeWidth={isSelected ? "2" : "1.5"}
                className="transition-transform duration-150 group-hover:scale-110"
              />

              {/* Icon / Glyph */}
              <text
                textAnchor="middle"
                dy="0.3em"
                fontSize={isSelected ? "13" : "11"}
                fill="#FFFFFF"
                className="pointer-events-none font-bold"
              >
                {isSelected ? "📄" : "•"}
              </text>

              {/* Title label */}
              <text
                textAnchor="middle"
                y={nodeRadius + 14}
                fontSize="10"
                fill={isSelected ? "#93C5FD" : "#CBD5E1"}
                fontWeight={isSelected ? "600" : "400"}
                className="pointer-events-none drop-shadow"
              >
                {node.title.length > 16 ? node.title.slice(0, 15) + "…" : node.title}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}
