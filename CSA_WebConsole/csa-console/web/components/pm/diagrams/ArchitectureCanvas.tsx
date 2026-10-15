"use client";

import React, { useMemo, useState } from "react";
import {
  ArrowRight,
  Cloud,
  Cpu,
  Database,
  Globe,
  HardDrive,
  Info,
  Layers,
  Lock,
  Minus,
  Network,
  Plus,
  RotateCcw,
  Server,
  Shield,
  X,
  Zap,
} from "lucide-react";
import { ArchitectureBoundary, ArchitectureGraph, ArchitectureNode } from "../types";
import { Pill } from "@/components/ui/kit";

type Props = {
  graph: ArchitectureGraph;
  className?: string;
  onSelectNode?: (node: ArchitectureNode | null) => void;
};

const CATEGORY_COLORS: Record<string, { bg: string; text: string; border: string; icon: React.ComponentType<{ className?: string }> }> = {
  compute: { bg: "bg-blue-50/80", text: "text-blue-700", border: "border-blue-200", icon: Cpu },
  database: { bg: "bg-emerald-50/80", text: "text-emerald-700", border: "border-emerald-200", icon: Database },
  storage: { bg: "bg-cyan-50/80", text: "text-cyan-700", border: "border-cyan-200", icon: HardDrive },
  network: { bg: "bg-purple-50/80", text: "text-purple-700", border: "border-purple-200", icon: Network },
  security: { bg: "bg-rose-50/80", text: "text-rose-700", border: "border-rose-200", icon: Shield },
  queue: { bg: "bg-amber-50/80", text: "text-amber-700", border: "border-amber-200", icon: Zap },
  analytics: { bg: "bg-indigo-50/80", text: "text-indigo-700", border: "border-indigo-200", icon: Server },
  custom: { bg: "bg-slate-50/80", text: "text-slate-700", border: "border-slate-200", icon: Cloud },
};

export function ArchitectureCanvas({ graph, className, onSelectNode }: Props) {
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [zoom, setZoom] = useState<number>(1);

  const selectedNode = useMemo(
    () => graph.nodes.find((n) => n.id === selectedNodeId) || null,
    [graph.nodes, selectedNodeId]
  );

  const boundariesById = useMemo(() => {
    const map = new Map<string, ArchitectureBoundary>();
    for (const b of graph.boundaries) {
      map.set(b.id, b);
    }
    return map;
  }, [graph.boundaries]);

  // Group nodes by boundary
  const nodesByBoundary = useMemo(() => {
    const map = new Map<string | null, ArchitectureNode[]>();
    for (const n of graph.nodes) {
      const bId = n.boundary_id || null;
      if (!map.has(bId)) {
        map.set(bId, []);
      }
      map.get(bId)!.push(n);
    }
    return map;
  }, [graph.nodes]);

  // Connected edges for selected node
  const selectedNodeEdges = useMemo(() => {
    if (!selectedNodeId) return { incoming: [], outgoing: [] };
    return {
      incoming: graph.edges.filter((e) => e.target === selectedNodeId),
      outgoing: graph.edges.filter((e) => e.source === selectedNodeId),
    };
  }, [graph.edges, selectedNodeId]);

  const handleNodeClick = (node: ArchitectureNode) => {
    const nextId = selectedNodeId === node.id ? null : node.id;
    setSelectedNodeId(nextId);
    if (onSelectNode) {
      onSelectNode(nextId ? node : null);
    }
  };

  return (
    <div className={`relative flex flex-col h-full w-full bg-slate-50/60 overflow-hidden ${className || ""}`}>
      {/* Canvas Toolbar */}
      <div className="absolute top-4 right-4 z-20 flex items-center gap-1.5 bg-white/90 backdrop-blur-sm px-2.5 py-1.5 rounded-xl border border-slate-200 shadow-sm text-xs text-slate-600">
        <button
          onClick={() => setZoom((z) => Math.max(0.6, z - 0.1))}
          className="p-1 hover:bg-slate-100 rounded-md transition-colors"
          title="Zoom Out"
        >
          <Minus className="w-3.5 h-3.5" />
        </button>
        <span className="font-mono text-[11px] w-9 text-center font-medium">
          {Math.round(zoom * 100)}%
        </span>
        <button
          onClick={() => setZoom((z) => Math.min(1.6, z + 0.1))}
          className="p-1 hover:bg-slate-100 rounded-md transition-colors"
          title="Zoom In"
        >
          <Plus className="w-3.5 h-3.5" />
        </button>
        <div className="w-[1px] h-3.5 bg-slate-200 mx-0.5" />
        <button
          onClick={() => setZoom(1)}
          className="p-1 hover:bg-slate-100 rounded-md transition-colors"
          title="Reset Zoom"
        >
          <RotateCcw className="w-3.5 h-3.5" />
        </button>
      </div>

      {/* Main Canvas Scroll Area */}
      <div className="flex-1 overflow-auto p-8 flex items-start justify-center">
        <div
          className="transition-transform duration-150 origin-top flex flex-col gap-6 max-w-5xl w-full"
          style={{ transform: `scale(${zoom})` }}
        >
          {/* Boundaries Grid */}
          <div className="space-y-6">
            {graph.boundaries.map((boundary) => {
              const boundaryNodes = nodesByBoundary.get(boundary.id) || [];
              const isVpc = boundary.kind === "vpc";
              const isSubnet = boundary.kind === "subnet";

              return (
                <div
                  key={boundary.id}
                  className={`rounded-2xl border transition-all ${
                    isVpc
                      ? "border-indigo-300/80 bg-gradient-to-b from-indigo-50/30 to-white shadow-sm p-6"
                      : isSubnet
                      ? boundary.is_public
                        ? "border-emerald-200 bg-emerald-50/20 p-5"
                        : "border-blue-200 bg-blue-50/20 p-5"
                      : "border-slate-200 bg-white/70 p-5"
                  }`}
                >
                  {/* Boundary Header */}
                  <div className="flex items-center justify-between mb-4">
                    <div className="flex items-center gap-2">
                      <span className="flex items-center justify-center w-6 h-6 rounded-lg bg-slate-200/60 text-slate-600">
                        {isVpc ? (
                          <Globe className="w-3.5 h-3.5 text-indigo-600" />
                        ) : isSubnet ? (
                          <Layers className="w-3.5 h-3.5 text-blue-600" />
                        ) : (
                          <Cloud className="w-3.5 h-3.5 text-slate-600" />
                        )}
                      </span>
                      <span className="font-semibold text-xs text-slate-800">
                        {boundary.label}
                      </span>
                      <Pill tone="neutral">{boundary.kind}</Pill>
                    </div>

                    <div className="flex items-center gap-2 text-[11px]">
                      {boundary.cidr && (
                        <code className="px-2 py-0.5 rounded bg-slate-100 text-slate-600 font-mono text-[10px]">
                          {boundary.cidr}
                        </code>
                      )}
                      {boundary.kind === "subnet" && (
                        <Pill tone={boundary.is_public ? "good" : "brand"}>
                          {boundary.is_public ? "Public" : "Private"}
                        </Pill>
                      )}
                    </div>
                  </div>

                  {/* Nodes Grid */}
                  <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-3.5">
                    {boundaryNodes.map((node) => {
                      const catStyle = CATEGORY_COLORS[node.category] || CATEGORY_COLORS.custom;
                      const Icon = catStyle.icon;
                      const isSelected = selectedNodeId === node.id;

                      return (
                        <div
                          key={node.id}
                          onClick={() => handleNodeClick(node)}
                          className={`cursor-pointer rounded-xl border p-4 transition-all duration-150 select-none ${
                            isSelected
                              ? "ring-2 ring-blue-600 border-blue-600 shadow-md bg-white scale-[1.02]"
                              : "border-slate-200 bg-white hover:border-slate-300 hover:shadow-xs"
                          }`}
                        >
                          <div className="flex items-start justify-between gap-2 mb-2">
                            <span
                              className={`w-7 h-7 rounded-lg flex items-center justify-center ${catStyle.bg} ${catStyle.text} border ${catStyle.border}`}
                            >
                              <Icon className="w-4 h-4" />
                            </span>
                            <span className="text-[10px] font-mono uppercase tracking-wider text-slate-400 font-medium">
                              {node.service_type}
                            </span>
                          </div>

                          <h4 className="font-medium text-xs text-slate-900 leading-snug line-clamp-1">
                            {node.label}
                          </h4>

                          {node.description && (
                            <p className="text-[11px] text-slate-500 mt-1 line-clamp-2 leading-relaxed">
                              {node.description}
                            </p>
                          )}
                        </div>
                      );
                    })}

                    {boundaryNodes.length === 0 && (
                      <div className="col-span-full py-4 text-center text-xs text-slate-400 italic">
                        No service nodes in this boundary
                      </div>
                    )}
                  </div>
                </div>
              );
            })}

            {/* Unassigned Nodes */}
            {(nodesByBoundary.get(null) || []).length > 0 && (
              <div className="rounded-2xl border border-dashed border-slate-300 p-5 bg-white/40">
                <div className="flex items-center gap-2 mb-4 text-xs font-semibold text-slate-500">
                  <Cloud className="w-4 h-4 text-slate-400" />
                  Global / External Services
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-3.5">
                  {(nodesByBoundary.get(null) || []).map((node) => {
                    const catStyle = CATEGORY_COLORS[node.category] || CATEGORY_COLORS.custom;
                    const Icon = catStyle.icon;
                    const isSelected = selectedNodeId === node.id;

                    return (
                      <div
                        key={node.id}
                        onClick={() => handleNodeClick(node)}
                        className={`cursor-pointer rounded-xl border p-4 transition-all duration-150 select-none ${
                          isSelected
                            ? "ring-2 ring-blue-600 border-blue-600 shadow-md bg-white scale-[1.02]"
                            : "border-slate-200 bg-white hover:border-slate-300 hover:shadow-xs"
                        }`}
                      >
                        <div className="flex items-start justify-between gap-2 mb-2">
                          <span
                            className={`w-7 h-7 rounded-lg flex items-center justify-center ${catStyle.bg} ${catStyle.text} border ${catStyle.border}`}
                          >
                            <Icon className="w-4 h-4" />
                          </span>
                          <span className="text-[10px] font-mono uppercase tracking-wider text-slate-400 font-medium">
                            {node.service_type}
                          </span>
                        </div>
                        <h4 className="font-medium text-xs text-slate-900 line-clamp-1">
                          {node.label}
                        </h4>
                        {node.description && (
                          <p className="text-[11px] text-slate-500 mt-1 line-clamp-2">
                            {node.description}
                          </p>
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
          </div>

          {/* Connected Flows / Edges Legend */}
          <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-xs">
            <h4 className="text-xs font-semibold text-slate-700 mb-2 flex items-center gap-1.5">
              <Network className="w-3.5 h-3.5 text-blue-600" />
              Connected Data Flows ({graph.edges.length})
            </h4>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-xs">
              {graph.edges.map((edge) => {
                const srcNode = graph.nodes.find((n) => n.id === edge.source);
                const tgtNode = graph.nodes.find((n) => n.id === edge.target);

                return (
                  <div
                    key={edge.id}
                    className={`flex items-center justify-between p-2 rounded-lg border text-[11px] ${
                      edge.crosses_trust_boundary
                        ? "border-rose-200 bg-rose-50/30 text-rose-900"
                        : "border-slate-100 bg-slate-50/60 text-slate-700"
                    }`}
                  >
                    <div className="flex items-center gap-1.5 truncate">
                      <span className="font-medium truncate">{srcNode?.label || edge.source}</span>
                      <ArrowRight className="w-3 h-3 text-slate-400 shrink-0" />
                      <span className="font-medium truncate">{tgtNode?.label || edge.target}</span>
                    </div>

                    <div className="flex items-center gap-1.5 shrink-0 ml-2">
                      {edge.protocol && (
                        <span className="font-mono text-[9px] px-1.5 py-0.5 rounded bg-white border border-slate-200 text-slate-600">
                          {edge.protocol}
                        </span>
                      )}
                      {edge.crosses_trust_boundary && (
                        <span title="Crosses Trust Boundary">
                          <Lock className="w-3 h-3 text-rose-600" />
                        </span>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      </div>

      {/* Selected Node Inspector Drawer */}
      {selectedNode && (
        <div className="absolute right-0 top-0 bottom-0 w-80 bg-white border-l border-slate-200 shadow-xl p-5 z-30 flex flex-col overflow-y-auto animate-in slide-in-from-right duration-200">
          <div className="flex items-center justify-between pb-3 border-b border-slate-100">
            <div className="flex items-center gap-2">
              <Info className="w-4 h-4 text-blue-600" />
              <h3 className="font-semibold text-xs text-slate-900">Node Inspector</h3>
            </div>
            <button
              onClick={() => setSelectedNodeId(null)}
              className="p-1 text-slate-400 hover:text-slate-600 rounded-md hover:bg-slate-100"
            >
              <X className="w-4 h-4" />
            </button>
          </div>

          <div className="mt-4 space-y-4 text-xs">
            <div>
              <label className="text-[11px] font-medium text-slate-400 uppercase tracking-wider block mb-1">
                Resource Name
              </label>
              <p className="font-semibold text-slate-900">{selectedNode.label}</p>
              <p className="font-mono text-[10px] text-slate-400 mt-0.5">ID: {selectedNode.id}</p>
            </div>

            <div className="grid grid-cols-2 gap-2">
              <div className="p-2.5 rounded-lg bg-slate-50 border border-slate-100">
                <span className="text-[10px] text-slate-400 block font-medium">Category</span>
                <span className="font-semibold text-slate-700 capitalize">{selectedNode.category}</span>
              </div>
              <div className="p-2.5 rounded-lg bg-slate-50 border border-slate-100">
                <span className="text-[10px] text-slate-400 block font-medium">Service</span>
                <span className="font-mono font-semibold text-slate-700 uppercase">
                  {selectedNode.service_type}
                </span>
              </div>
            </div>

            {selectedNode.boundary_id && (
              <div>
                <label className="text-[11px] font-medium text-slate-400 uppercase tracking-wider block mb-1">
                  Enclosing Boundary
                </label>
                <div className="p-2 rounded-lg bg-slate-50 border border-slate-100 text-slate-700 flex items-center justify-between">
                  <span className="font-medium">
                    {boundariesById.get(selectedNode.boundary_id)?.label || selectedNode.boundary_id}
                  </span>
                  <Pill tone="brand">{boundariesById.get(selectedNode.boundary_id)?.kind}</Pill>
                </div>
              </div>
            )}

            {selectedNode.description && (
              <div>
                <label className="text-[11px] font-medium text-slate-400 uppercase tracking-wider block mb-1">
                  Description
                </label>
                <p className="text-slate-600 leading-relaxed bg-slate-50 p-2.5 rounded-lg border border-slate-100">
                  {selectedNode.description}
                </p>
              </div>
            )}

            {/* Attached Edges */}
            <div>
              <label className="text-[11px] font-medium text-slate-400 uppercase tracking-wider block mb-2">
                Connected Flows
              </label>
              <div className="space-y-1.5">
                {selectedNodeEdges.incoming.map((e) => (
                  <div key={e.id} className="text-[11px] p-2 rounded bg-slate-50 border border-slate-100 flex items-center justify-between">
                    <span className="text-slate-600 truncate">← From {e.source}</span>
                    <span className="font-mono text-[9px] text-blue-600">{e.protocol || "Link"}</span>
                  </div>
                ))}
                {selectedNodeEdges.outgoing.map((e) => (
                  <div key={e.id} className="text-[11px] p-2 rounded bg-slate-50 border border-slate-100 flex items-center justify-between">
                    <span className="text-slate-600 truncate">→ To {e.target}</span>
                    <span className="font-mono text-[9px] text-blue-600">{e.protocol || "Link"}</span>
                  </div>
                ))}
                {selectedNodeEdges.incoming.length === 0 && selectedNodeEdges.outgoing.length === 0 && (
                  <p className="text-slate-400 italic text-[11px]">No active data flows connected</p>
                )}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
