/** Button looks shared by the assistant's views (the dock, the Overview page) and its composer. */

/** The corner the dock's launcher and its panel share: 20 px in, or clear of a notch or home bar. */
export const corner =
  "bottom-[max(1.25rem,env(safe-area-inset-bottom))] right-[max(1.25rem,env(safe-area-inset-right))]";

/** The filled send button and the retry row's main button. */
export const sendLook =
  "inline-flex h-10 min-w-10 shrink-0 items-center justify-center gap-2 rounded-[var(--fs-radius-round)] bg-[var(--fs-primary)] text-sm font-medium text-[color:var(--fs-on-primary)] transition-colors duration-150 hover:bg-[var(--fs-primary-hover)] focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--fs-focus)] active:scale-[0.97] disabled:cursor-not-allowed disabled:bg-[var(--fs-disabled-bg)] disabled:text-[color:var(--fs-disabled-text)] pointer-coarse:h-11";

/** The small outlined button: "New conversation" in a view's header, and the retry row's other choice. */
export const headerButton =
  "inline-flex min-h-9 items-center rounded-[var(--fs-radius-round)] border border-[color:var(--fs-border-control)] px-2.5 text-[13px] font-medium text-[color:var(--fs-text-secondary)] transition-colors duration-150 hover:bg-[var(--fs-surface-blue)] hover:text-[color:var(--fs-text)] focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--fs-focus)] disabled:cursor-not-allowed disabled:opacity-50 pointer-coarse:min-h-11";
