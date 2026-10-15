"use client";

import React, { useState } from "react";
import { StarterTemplate } from "../types";
import { Pill } from "../../ui/kit";

type TemplatePickerModalProps = {
  isOpen: boolean;
  onClose: () => void;
  templates: StarterTemplate[];
  onSelectTemplate: (template: StarterTemplate) => void;
};

export function TemplatePickerModal({
  isOpen,
  onClose,
  templates,
  onSelectTemplate,
}: TemplatePickerModalProps) {
  const [selectedId, setSelectedId] = useState<string>(templates[0]?.id || "adr");

  if (!isOpen) return null;

  const currentTemplate = templates.find((t) => t.id === selectedId) || templates[0];

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-900/40 backdrop-blur-sm animate-in fade-in duration-150">
      <div className="bg-white rounded-2xl shadow-2xl border border-slate-200/80 w-full max-w-4xl max-h-[85vh] flex flex-col overflow-hidden">
        {/* Header */}
        <div className="px-6 py-4 border-b border-slate-100 flex items-center justify-between bg-slate-50/50">
          <div>
            <h2 className="text-base font-semibold text-slate-900 flex items-center gap-2">
              <span className="text-lg">📋</span> Seed from Starter Template
            </h2>
            <p className="text-xs text-slate-500 mt-0.5">
              Choose an Obsidian-compatible structural template to accelerate your notes.
            </p>
          </div>
          <button
            onClick={onClose}
            className="text-slate-400 hover:text-slate-600 p-1.5 rounded-lg hover:bg-slate-100 transition-colors"
          >
            ✕
          </button>
        </div>

        {/* Content Body: Split layout */}
        <div className="flex-1 flex overflow-hidden divide-x divide-slate-100">
          {/* Template List */}
          <div className="w-72 overflow-y-auto p-4 space-y-2 bg-slate-50/30">
            {templates.map((tmpl) => {
              const active = tmpl.id === selectedId;
              return (
                <button
                  key={tmpl.id}
                  type="button"
                  onClick={() => setSelectedId(tmpl.id)}
                  className={`w-full text-left p-3 rounded-xl border transition-all ${
                    active
                      ? "bg-white border-blue-500 shadow-sm ring-1 ring-blue-500/20"
                      : "bg-white/80 border-slate-200 hover:border-slate-300 hover:bg-white"
                  }`}
                >
                  <div className="font-medium text-sm text-slate-900">{tmpl.name}</div>
                  <div className="text-xs text-slate-500 line-clamp-2 mt-1">{tmpl.description}</div>
                  <div className="flex flex-wrap gap-1 mt-2">
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-slate-100 text-slate-600 font-mono">
                      {tmpl.folder}
                    </span>
                    {tmpl.tags.slice(0, 2).map((tg) => (
                      <span key={tg} className="text-[10px] px-1.5 py-0.5 rounded bg-blue-50 text-blue-600 font-medium">
                        #{tg}
                      </span>
                    ))}
                  </div>
                </button>
              );
            })}
          </div>

          {/* Template Preview */}
          <div className="flex-1 flex flex-col overflow-hidden p-6 bg-white">
            {currentTemplate && (
              <>
                <div className="flex items-center justify-between pb-3 border-b border-slate-100">
                  <div>
                    <h3 className="text-sm font-semibold text-slate-900">{currentTemplate.name}</h3>
                    <p className="text-xs text-slate-500">{currentTemplate.description}</p>
                  </div>
                  <div className="flex items-center gap-2">
                    <Pill tone="brand">Target Folder: {currentTemplate.folder}</Pill>
                  </div>
                </div>

                <div className="flex-1 overflow-y-auto my-4 p-4 rounded-xl bg-slate-50/80 border border-slate-200/60 font-mono text-xs text-slate-800 whitespace-pre-wrap leading-relaxed">
                  {currentTemplate.body}
                </div>
              </>
            )}
          </div>
        </div>

        {/* Footer */}
        <div className="px-6 py-3 border-t border-slate-100 bg-slate-50/50 flex items-center justify-between">
          <button
            type="button"
            onClick={onClose}
            className="px-4 py-2 text-xs font-medium text-slate-600 hover:text-slate-800 rounded-lg hover:bg-slate-100 transition-colors"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={() => {
              if (currentTemplate) {
                onSelectTemplate(currentTemplate);
                onClose();
              }
            }}
            className="px-4 py-2 text-xs font-medium text-white bg-blue-600 hover:bg-blue-700 rounded-lg shadow-sm transition-colors flex items-center gap-1.5"
          >
            <span>Use Template</span>
            <span>→</span>
          </button>
        </div>
      </div>
    </div>
  );
}
