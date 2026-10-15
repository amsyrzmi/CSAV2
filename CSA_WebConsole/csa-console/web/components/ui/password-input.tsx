"use client";
import { useState } from "react";
import { Eye, EyeOff } from "lucide-react";
import { input } from "./input";

export function PasswordInput(props: Omit<React.InputHTMLAttributes<HTMLInputElement>, "type" | "className">) {
  const [show, setShow] = useState(false);
  return (
    <div className="relative">
      <input {...props} type={show ? "text" : "password"} className={input + " h-10 pr-10"} />
      <button type="button" onClick={() => setShow((s) => !s)} aria-label={show ? "Hide password" : "Show password"}
        className="absolute right-1.5 top-1/2 -translate-y-1/2 rounded-md p-1.5 text-ink-400 hover:bg-ink-100 hover:text-ink-700">
        {show ? <EyeOff size={15} /> : <Eye size={15} />}
      </button>
    </div>
  );
}

export function passwordStrength(pw: string) {
  return [pw.length >= 12, /[A-Z]/.test(pw) && /[a-z]/.test(pw), /\d/.test(pw), /[^A-Za-z0-9]/.test(pw)].filter(Boolean).length;
}

export function StrengthMeter({ value }: { value: number }) {
  const colors = ["bg-red-400", "bg-amber-400", "bg-amber-400", "bg-emerald-400", "bg-emerald-500"];
  const labels = ["Too short", "Weak", "Fair", "Good", "Strong"];
  return (
    <div className="mt-2 flex items-center gap-3">
      <div className="grid flex-1 grid-cols-4 gap-1">
        {[0, 1, 2, 3].map((i) => <span key={i} className={`h-1 rounded-full transition-colors ${i < value ? colors[value] : "bg-ink-200"}`} />)}
      </div>
      <span className="w-14 text-right text-[11px] font-medium text-ink-500">{labels[value]}</span>
    </div>
  );
}
