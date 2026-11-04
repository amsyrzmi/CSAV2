import { tintIndex } from "@/lib/customer-identity";
import { initials } from "@/lib/team-view";

/**
 * A round badge of initials for a team member, tinted from their id so they keep their colour.
 * Decorative: the name is printed beside it. Tints are theme tokens.
 */
export default function MemberAvatar({
  id,
  name,
  email,
  testId,
}: {
  id: string;
  name: string;
  email: string;
  testId?: string;
}) {
  const tint = tintIndex(id) + 1;
  return (
    <span
      aria-hidden="true"
      data-testid={testId}
      className="inline-flex size-10 shrink-0 select-none items-center justify-center rounded-full font-(family-name:--fs-font-heading) text-sm font-bold leading-none"
      style={{ background: `var(--fs-customer-${tint}-bg)`, color: `var(--fs-customer-${tint}-text)` }}
    >
      {initials(name, email)}
    </span>
  );
}
