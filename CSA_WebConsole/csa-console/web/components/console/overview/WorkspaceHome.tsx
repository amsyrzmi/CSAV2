import Link from "next/link";
import type { Me } from "@/lib/identity-contracts";
import { dockTenant } from "@/lib/assistant-view";
import { navigationFor } from "@/lib/navigation";
import { homeLinks } from "@/lib/overview-home";
import type { PackageState } from "@/lib/services-api";
import { AssistantTile } from "../chat/ChatEmptyState";

const linkLook =
  "flex min-h-12 items-center rounded-[var(--fs-radius-panel)] border border-[color:var(--fs-border-control)] bg-[var(--fs-surface)] px-4 py-3 text-sm font-medium text-[color:var(--fs-text)] transition-colors duration-150 hover:border-[color:var(--fs-primary)] hover:bg-[var(--fs-surface-blue)] focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--fs-focus)] pointer-coarse:min-h-14";

/**
 * Overview for a company without Customer support, which has no customers to chat as: links to
 * the pages it does have, and, where the assistant dock renders for this person and company, a
 * pointer to it at the side of the screen. The shell decides that with `dockTenant` (no dock for a
 * read-only member or when the assistant is off), so the sentence asks the same question; `tenant`
 * is the `?client_id=` a platform admin has chosen, which is what the shell passes. A platform
 * admin sees what a member of the chosen company would.
 */
export function WorkspaceHome({ me, packages, tenant }: {
  me: Me;
  packages: PackageState[];
  tenant: string;
}) {
  const platform = me.role === "platform_admin";
  const rail = navigationFor(platform ? { ...me, role: "tenant_admin" } : me, packages);
  const links = homeLinks(rail, platform, tenant);
  const hasDock = dockTenant(me, tenant) !== null;
  return (
    <div data-testid="workspace-home" className="mx-auto flex w-full max-w-xl flex-col items-center gap-5 p-6 text-center sm:py-12">
      <AssistantTile />
      <h2 className="font-(family-name:--fs-font-heading) text-xl font-semibold text-[color:var(--fs-text)] sm:text-2xl">
        Your workspace
      </h2>
      <ul aria-label="Your pages" className="grid w-full gap-3 text-left sm:grid-cols-2">
        {links.map((link) => (
          <li key={link.key}>
            <Link href={link.href} data-testid={`workspace-home-${link.key}`} className={linkLook}>
              {link.label}
            </Link>
          </li>
        ))}
      </ul>
      {hasDock && (
        <p className="text-sm text-[color:var(--fs-text-secondary)]">Ask the assistant at the side of the screen.</p>
      )}
    </div>
  );
}
