export type AgentEye = { x: number; y: number; radius: number }

export default function AgentFace({
  eyes,
  accent,
}: {
  eyes: readonly AgentEye[]
  accent: string
}) {
  return (
    <g className="agent-face" data-eye-count={eyes.length}>
      <g className="agent-eyes">
        {eyes.map(({ x, y, radius }, index) => (
          <g
            key={index}
            className="agent-eye"
            transform={`translate(${x} ${y})`}
          >
            <circle
              className="agent-eye-well"
              r={radius * 1.8 + 1}
              fill="#15203A"
            />
            <circle
              className="agent-eye-halo"
              r={radius * 1.38}
              fill={accent}
              opacity=".45"
            />
            <g className="agent-eye-lid">
              <g className="agent-gaze">
                <circle className="agent-eye-iris" r={radius} fill="#FFFFFF" />
              </g>
            </g>
          </g>
        ))}
      </g>
    </g>
  )
}
