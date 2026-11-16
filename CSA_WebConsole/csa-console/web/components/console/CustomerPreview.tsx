"use client";

import { ChevronDown } from "lucide-react";
import dynamic from "next/dynamic";
import { useSearchParams } from "next/navigation";
import { useId, useState } from "react";
import type { Me } from "@/lib/identity-contracts";
import { secondary } from "./ui";

// The chat is a large client bundle and most visits to Services never open it: load it on demand.
const SandboxWorkspace = dynamic(
  () => import("./SandboxWorkspace").then((module) => module.SandboxWorkspace),
  {
    ssr: false,
    loading: () => <p role="status" className="p-6 text-center text-sm text-[color:var(--fs-text-secondary)]">Loading</p>,
  },
);

/**
 * A collapsed section on Services: the private chat where staff talk to their own bot as a
 * customer would. The chat mounts (and so reads its conversations) only while the section is
 * open. It opens when the URL's `section` becomes `preview`, at mount or later (the assistant's
 * "open the customer preview" pushes it to a Services page already shown); the parameter never
 * closes it, and a collapse by hand holds until the parameter changes again. `tenantId` is the
 * chosen company for a platform administrator and null for a member.
 */
export function CustomerPreview({ me, tenantId }: { me: Me; tenantId: string | null }) {
  const section = useSearchParams().get("section");
  const [open, setOpen] = useState(section === "preview");
  // The parameter last acted on: a changed one is handled during render, before paint.
  const [seenSection, setSeenSection] = useState(section);
  if (section !== seenSection) {
    setSeenSection(section);
    if (section === "preview") setOpen(true);
  }
  const regionId = useId();
  return (
    <section data-testid="customer-preview-section" className="mt-4">
      <button type="button" className={`${secondary} gap-2`} aria-expanded={open} aria-controls={regionId}
        data-testid="customer-preview-toggle" onClick={() => setOpen((value) => !value)}>
        <ChevronDown aria-hidden="true" className={`size-4 transition-transform ${open ? "" : "-rotate-90"}`} />
        Customer preview: talk to your bot as a customer would
      </button>
      <div id={regionId} hidden={!open} data-testid="customer-preview"
        className="mt-3 h-[32rem] overflow-hidden rounded-[14px] border border-[var(--fs-border)] bg-[var(--fs-surface)]">
        {open && <SandboxWorkspace me={me} tenant={tenantId ?? undefined} />}
      </div>
    </section>
  );
}
