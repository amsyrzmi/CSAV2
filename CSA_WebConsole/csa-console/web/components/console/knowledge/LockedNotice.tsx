"use client";

import { Notice, secondary } from "../ui";
import type { Slot } from "./useKbSlot";

/** The sentence the server sends when the package is off, with a refresh. Every Business knowledge page shows it alone. */
export default function LockedNotice({ slot, onRefresh }: { slot: Slot<unknown>; onRefresh: () => void }) {
  return (
    <div className="space-y-5">
      <Notice error testId="kb-locked">{slot.error}</Notice>
      <button type="button" className={secondary} onClick={onRefresh}>Refresh business knowledge</button>
    </div>
  );
}
