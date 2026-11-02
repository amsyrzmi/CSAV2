"use client";
import { useState } from "react";
import { Eye, EyeOff } from "lucide-react";
import { field } from "@/components/console/ui";

const ring =
  "focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--fs-focus)]";

type Props = Omit<React.InputHTMLAttributes<HTMLInputElement>, "type" | "className"> & {
  /** What the show/hide button names, for example "new password": the button reads "Show new password". */
  fieldName: string;
};

/**
 * A password input with its own show/hide button. Each field keeps its own state, so showing
 * one never reveals another. The button label stays the same and `aria-pressed` carries the state.
 */
export function PasswordField({ fieldName, ...props }: Props) {
  const [shown, setShown] = useState(false);
  const Icon = shown ? EyeOff : Eye;
  return (
    <div className="relative">
      <input {...props} type={shown ? "text" : "password"} className={`${field} pr-12`} />
      <button
        type="button"
        aria-label={`Show ${fieldName}`}
        aria-pressed={shown}
        onClick={() => setShown((value) => !value)}
        className={`absolute right-1 top-1/2 inline-flex size-8 -translate-y-1/2 items-center justify-center rounded-[var(--fs-radius-control)] text-[color:var(--fs-text-secondary)] hover:bg-brand-50 hover:text-ink-900 pointer-coarse:size-10 ${ring}`}
      >
        <Icon size={16} aria-hidden="true" />
      </button>
    </div>
  );
}
