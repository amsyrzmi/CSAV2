/** Copy the SVGs into public/fourstacks/agents in the product application. */
export type AgentId =
  'support' | 'knowledge' | 'orders' | 'tickets' | 'ecommerce' | 'invoices'
export type AvatarSize = 24 | 32 | 40 | 48 | 64 | 96

export default function AgentAvatar({
  agentId,
  size = 40,
  label = '',
  assetBase = '/fourstacks/agents',
}: {
  agentId: AgentId
  size?: AvatarSize
  /** Empty when the same agent name is already printed beside the avatar. */
  label?: string
  assetBase?: string
}) {
  return (
    <span className="fs-avatar" style={{ width: size, height: size }}>
      <img
        src={`${assetBase}/${agentId}.svg`}
        alt={label}
        width={size}
        height={size}
        decoding="async"
      />
    </span>
  )
}
