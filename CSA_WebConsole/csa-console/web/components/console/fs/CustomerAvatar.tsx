import { CUSTOMER_TINTS, initialsFor, tintIndex } from "@/lib/customer-identity";
import type { AvatarSize } from "./AgentAvatar";

/**
 * A customer is a circle of initials in one of four blues, or a dashed circle with "?"
 * when there is no real name. Agents are never round, so the two cannot be confused.
 * Decorative unless a label is given: the row text already carries the name.
 */
export default function CustomerAvatar({
  id,
  name,
  size = 40,
  label,
}: {
  id: string;
  name: string | null;
  size?: AvatarSize;
  /** Only when no name is printed beside the avatar. */
  label?: string;
}) {
  const initials = initialsFor(name);
  const tint = CUSTOMER_TINTS[tintIndex(id)];
  const accessible = label ? { role: "img", "aria-label": label } : { "aria-hidden": true };
  return (
    <span
      {...accessible}
      style={{
        display: "inline-flex",
        flex: "0 0 auto",
        alignItems: "center",
        justifyContent: "center",
        verticalAlign: "middle",
        boxSizing: "border-box",
        width: size,
        height: size,
        borderRadius: "50%",
        fontFamily: "var(--fs-font-heading, inherit)",
        fontWeight: 700,
        fontSize: Math.round(size * 0.38),
        lineHeight: 1,
        userSelect: "none",
        ...(initials
          ? { background: tint.background, color: tint.color }
          : { background: "#FFFFFF", color: "#566274", border: "1.5px dashed #7B8CA5" }),
      }}
    >
      {initials ?? "?"}
    </span>
  );
}
