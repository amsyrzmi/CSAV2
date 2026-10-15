import { useId } from 'react'
// The included engine is exported from the canonical landing-page modules.
import BlobAgent from './agent-engine/BlobAgent'
import useAgentGaze from './agent-engine/useAgentGaze'
import type { AgentId } from './AgentAvatar'

const artKind: Record<AgentId, number> = {
  support: 1,
  knowledge: 5,
  orders: 3,
  tickets: 0,
  ecommerce: 2,
  invoices: 4,
}

/** Use for a selected agent's detail panel, rather than each row of a table. */
export default function WorkingAgentAvatar({
  agentId,
  size = 192,
  paused = true,
  label,
}: {
  agentId: AgentId
  size?: number
  paused?: boolean
  /** Omit when adjacent text already names the agent. */
  label?: string
}) {
  const titleId = `working-agent-${useId().replace(/:/g, '')}`
  const gaze = useAgentGaze<SVGSVGElement>(paused)
  return (
    <svg
      {...gaze}
      className="fs-working-avatar"
      viewBox="-32 -8 224 208"
      width={size}
      height={(size * 208) / 224}
      role={label ? 'img' : undefined}
      aria-labelledby={label ? titleId : undefined}
      aria-hidden={label ? undefined : true}
      data-motion-paused={paused}
    >
      {label && <title id={titleId}>{label}</title>}
      <BlobAgent kind={artKind[agentId]} paused={paused} />
    </svg>
  )
}
