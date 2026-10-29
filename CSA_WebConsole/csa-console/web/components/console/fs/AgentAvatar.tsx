import Image from "next/image";
import type { AgentId } from "@/lib/agent-avatars";

export type AvatarSize = 24 | 32 | 40 | 48 | 64 | 96;

/**
 * An agent is the creature on a pale rounded tile, never a circle, so it cannot be
 * mistaken for a customer's round initials. The silhouette is not cropped.
 */
export default function AgentAvatar({
  agentId,
  size = 40,
  label = "",
}: {
  agentId: AgentId;
  size?: AvatarSize;
  /** Empty when the same agent name is already printed beside the avatar. */
  label?: string;
}) {
  return (
    <span
      style={{
        display: "inline-flex",
        flex: "0 0 auto",
        alignItems: "center",
        justifyContent: "center",
        verticalAlign: "middle",
        width: size,
        height: size,
        borderRadius: 10,
        background: "linear-gradient(160deg,#EDF2FF,#DCE9FF)",
        overflow: "hidden",
      }}
    >
      <Image
        src={`/fourstacks/agents/${agentId}.svg`}
        alt={label}
        width={size}
        height={size}
        unoptimized
        style={{ display: "block", width: "100%", height: "100%", objectFit: "contain" }}
      />
    </span>
  );
}
