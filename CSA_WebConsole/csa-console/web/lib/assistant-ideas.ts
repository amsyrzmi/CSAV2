/** Starting ideas for the assistant: which of the shipped list a person is offered (pure, no I/O).
 *
 * The list itself is `assistant-ideas.json`. Every idea's text is the `message` of an assistant
 * smoke case (tests/test_assistant_ideas.py), so an idea can be sent as it stands. */
import type { Language } from "@/lib/assistant-copy";
import type { Me } from "@/lib/identity-contracts";
import type { PackageState } from "@/lib/services-api";
import type { TabKey } from "@/lib/workspace-api";

export interface Idea {
  id: string;
  tab: TabKey | "any";
  roles: string[];
  packages: string[];
  en: string;
  ms: string;
}

const DEFAULT_LIMIT = 4;

/** The ideas this person may be offered: those for the current tab first, then the ones for any
 * page, each group in file order. A platform admin counts as a tenant admin; a read-only member
 * (who has no assistant) and a missing role get none. */
export function ideasFor(
  ideas: readonly Idea[],
  opts: {
    tab: TabKey | null;
    role: Me["role"];
    packages: PackageState[];
    language: Language;
    limit?: number;
  },
): { id: string; text: string }[] {
  const role = opts.role === "platform_admin" ? "tenant_admin" : opts.role;
  if (role !== "tenant_admin" && role !== "tenant_staff") return [];
  const active = new Set(opts.packages.filter((p) => p.status === "active").map((p) => p.key));
  const allowed = ideas.filter(
    (idea) => idea.roles.includes(role) && idea.packages.every((key) => active.has(key)),
  );
  const here = opts.tab === null ? [] : allowed.filter((idea) => idea.tab === opts.tab);
  const anywhere = allowed.filter((idea) => idea.tab === "any");
  return [...here, ...anywhere]
    .slice(0, Math.max(0, opts.limit ?? DEFAULT_LIMIT))
    .map((idea) => ({ id: idea.id, text: idea[opts.language] }));
}
