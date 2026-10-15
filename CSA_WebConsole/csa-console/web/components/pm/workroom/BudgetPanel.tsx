"use client";

import React from "react";
import { Clock, Zap, DollarSign, RefreshCw } from "lucide-react";
import { Card } from "@/components/ui/kit";
import { WorkroomBudgetStatus, WorkroomRun } from "../types";

interface BudgetPanelProps {
  run?: WorkroomRun | null;
  budgetStatus?: WorkroomBudgetStatus | null;
}

export function BudgetPanel({ run, budgetStatus }: BudgetPanelProps) {
  const maxTokens = (run?.budgets?.max_tokens as number) || budgetStatus?.limits?.max_tokens_per_run || 100000;
  const maxWallTime = (run?.budgets?.max_wall_time_sec as number) || budgetStatus?.limits?.max_wall_time_sec || 900;
  const maxCost = (run?.budgets?.max_cost_usd as number) || budgetStatus?.limits?.max_cost_usd || 1.0;
  const maxRepair = run?.max_repair_cycles || budgetStatus?.limits?.max_repair_cycles || 2;

  const usedTokens = run?.usage?.tokens_used || 0;
  const usedWallTime = run?.usage?.wall_time_sec || 0;
  const usedCost = run?.usage?.estimated_cost_usd || 0.0;
  const repairCycles = run?.repair_cycles || 0;

  const tokenPct = Math.min(100, Math.round((usedTokens / maxTokens) * 100));
  const timePct = Math.min(100, Math.round((usedWallTime / maxWallTime) * 100));
  const costPct = Math.min(100, Math.round((usedCost / maxCost) * 100));
  const repairPct = Math.min(100, Math.round((repairCycles / maxRepair) * 100));

  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
      {/* Wall Time Meter */}
      <Card className="p-3 border border-ink-200 bg-white">
        <div className="flex items-center justify-between text-xs text-ink-500">
          <span className="flex items-center gap-1.5 font-medium">
            <Clock size={13} className="text-sky-600" />
            Wall Time
          </span>
          <span className="font-mono text-[11px] text-ink-700">
            {usedWallTime}s / {maxWallTime}s
          </span>
        </div>
        <div className="mt-2 w-full h-1.5 bg-ink-100 rounded-full overflow-hidden">
          <div
            className={`h-full rounded-full transition-all duration-500 ${
              timePct > 80 ? "bg-rose-500" : timePct > 50 ? "bg-amber-500" : "bg-sky-500"
            }`}
            style={{ width: `${Math.max(4, timePct)}%` }}
          />
        </div>
      </Card>

      {/* Token Meter */}
      <Card className="p-3 border border-ink-200 bg-white">
        <div className="flex items-center justify-between text-xs text-ink-500">
          <span className="flex items-center gap-1.5 font-medium">
            <Zap size={13} className="text-purple-600" />
            Tokens Used
          </span>
          <span className="font-mono text-[11px] text-ink-700">
            {usedTokens.toLocaleString()} / {maxTokens.toLocaleString()}
          </span>
        </div>
        <div className="mt-2 w-full h-1.5 bg-ink-100 rounded-full overflow-hidden">
          <div
            className={`h-full rounded-full transition-all duration-500 ${
              tokenPct > 80 ? "bg-rose-500" : tokenPct > 50 ? "bg-amber-500" : "bg-purple-500"
            }`}
            style={{ width: `${Math.max(4, tokenPct)}%` }}
          />
        </div>
      </Card>

      {/* Cost Meter */}
      <Card className="p-3 border border-ink-200 bg-white">
        <div className="flex items-center justify-between text-xs text-ink-500">
          <span className="flex items-center gap-1.5 font-medium">
            <DollarSign size={13} className="text-emerald-600" />
            Estimated Cost
          </span>
          <span className="font-mono text-[11px] text-ink-700">
            ${usedCost.toFixed(3)} / ${maxCost.toFixed(2)}
          </span>
        </div>
        <div className="mt-2 w-full h-1.5 bg-ink-100 rounded-full overflow-hidden">
          <div
            className={`h-full rounded-full transition-all duration-500 ${
              costPct > 80 ? "bg-rose-500" : costPct > 50 ? "bg-amber-500" : "bg-emerald-500"
            }`}
            style={{ width: `${Math.max(4, costPct)}%` }}
          />
        </div>
      </Card>

      {/* Repair Cycle & Concurrency Meter */}
      <Card className="p-3 border border-ink-200 bg-white">
        <div className="flex items-center justify-between text-xs text-ink-500">
          <span className="flex items-center gap-1.5 font-medium">
            <RefreshCw size={13} className="text-amber-600" />
            Repair Cycles
          </span>
          <span className="font-mono text-[11px] text-ink-700">
            {repairCycles} / {maxRepair}
          </span>
        </div>
        <div className="mt-2 w-full h-1.5 bg-ink-100 rounded-full overflow-hidden">
          <div
            className={`h-full rounded-full transition-all duration-500 ${
              repairCycles >= maxRepair ? "bg-rose-500" : "bg-amber-500"
            }`}
            style={{ width: `${Math.max(4, repairPct)}%` }}
          />
        </div>
      </Card>
    </div>
  );
}
