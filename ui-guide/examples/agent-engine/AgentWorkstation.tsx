import { getWorkPose, invoiceTrace } from './agentWork'

export default function AgentWorkstation({
  kind,
  color,
  accent,
}: {
  kind: number
  color: string
  accent: string
}) {
  const rest = getWorkPose(kind, null)
  const part = (name: string) => {
    const pose = rest.parts[name]
    return {
      'data-work-part': name,
      transform: pose?.transform,
      opacity: pose?.opacity,
      strokeDashoffset:
        pose?.progress === undefined ? undefined : 1 - pose.progress,
    }
  }
  const ink = '#263952'
  const border = '#C7D3E0'
  const paper = '#FFFFFF'

  if (kind === 0)
    return (
      <g className="agent-workstation" strokeLinejoin="round">
        <rect
          x="-16"
          y="151"
          width="40"
          height="23"
          rx="4"
          fill="#D6EAF6"
          stroke={border}
        />
        <rect
          x="-11"
          y="133"
          width="30"
          height="22"
          rx="3"
          fill={paper}
          stroke={border}
        />
        <path
          d="m-10 136 14 10 14-10M-16 161h40"
          fill="none"
          stroke={color}
          strokeWidth="1.2"
        />
        <rect
          x="132"
          y="151"
          width="43"
          height="23"
          rx="4"
          fill="#D6EAF6"
          stroke={border}
        />
        <g {...part('mail-sorted')}>
          <rect
            x="139"
            y="150"
            width="29"
            height="18"
            rx="2"
            fill={paper}
            stroke={color}
          />
          <path d="m140 152 13 9 13-9" fill="none" stroke={color} />
          <circle cx="169" cy="151" r="7" fill={color} />
          <path
            d="m166 151 2 2 4-4"
            fill="none"
            stroke="white"
            strokeWidth="1.3"
          />
        </g>
        <path d="M132 161h43" stroke={color} strokeWidth="1.2" />
        <g {...part('mail-transfer')}>
          <rect
            x="-29"
            y="0"
            width="29"
            height="18"
            rx="2"
            fill={paper}
            stroke={color}
            strokeWidth="1.2"
          />
          <path
            d="m-28 2 13 9 13-9"
            fill="none"
            stroke={color}
            strokeWidth="1.2"
          />
        </g>
      </g>
    )
  if (kind === 1)
    return (
      <g className="agent-workstation" strokeLinejoin="round">
        <rect x="134" y="99" width="49" height="40" rx="5" fill={ink} />
        <rect x="137" y="102" width="43" height="32" rx="3" fill={paper} />
        <path d="M156 139v10m-9 0h19" stroke={ink} strokeWidth="2.2" />
        <path d="M142 108h17v9h-11l-4 3v-3h-2Z" fill="#E4EDFF" />
        <path d="M146 111h9m-9 3h6" stroke={color} strokeWidth="1.2" />
        <path
          d="M149 123h24m-24 5h16"
          stroke={color}
          strokeWidth="1.4"
          strokeLinecap="round"
          pathLength="1"
          strokeDasharray="1"
          {...part('reply-line')}
        />
        <circle cx="174" cy="128" r="3" fill={color} {...part('reply-ready')} />
        <path
          d="M26 148h104l8 23H18Z"
          fill={paper}
          stroke={border}
          strokeWidth="1.2"
        />
        {[0, 1, 2, 3, 4, 5, 6, 7].map((i) => (
          <path
            key={i}
            d={`M${31 + i * 12} 153h7m-8 7h8`}
            stroke="#C5D4EA"
            strokeWidth="3"
            strokeLinecap="round"
          />
        ))}
        <rect
          x="46"
          y="152"
          width="11"
          height="6"
          rx="1.5"
          fill={color}
          {...part('key-left')}
        />
        <rect
          x="93"
          y="152"
          width="11"
          height="6"
          rx="1.5"
          fill={accent}
          {...part('key-right')}
        />
        <path
          d="M61 166h34"
          stroke={border}
          strokeWidth="2.5"
          strokeLinecap="round"
        />
      </g>
    )
  if (kind === 2)
    return (
      <g className="agent-workstation" strokeLinejoin="round">
        <rect
          x="-18"
          y="112"
          width="53"
          height="65"
          rx="5"
          fill={paper}
          stroke={border}
          strokeWidth="1.2"
        />
        <path d="M-18 121h53" stroke={border} />
        <circle cx="-12" cy="117" r="1.5" fill={color} />
        <path
          d="M-5 117h22"
          stroke={border}
          strokeWidth="2"
          strokeLinecap="round"
        />
        <rect x="-10" y="126" width="37" height="15" rx="3" fill="#E6F3FF" />
        <path
          d="M3 131h11v8H3Zm2 0v-2a3 3 0 0 1 6 0v2"
          fill="none"
          stroke={color}
          strokeWidth="1.2"
        />
        <rect
          x="-10"
          y="145"
          width="37"
          height="8"
          rx="3"
          fill={color}
          {...part('catalog-button')}
        />
        <path
          d="M-8 160h29m-29 6h20"
          stroke={border}
          strokeWidth="2"
          strokeLinecap="round"
        />
        <rect
          x="135"
          y="132"
          width="45"
          height="39"
          rx="5"
          fill={paper}
          stroke={border}
        />
        <path
          d="M142 139h24"
          stroke={border}
          strokeWidth="2"
          strokeLinecap="round"
        />
        <rect
          x="142"
          y="146"
          width="30"
          height="8"
          rx="4"
          fill={color}
          {...part('stock-button')}
        />
        <path
          d="m146 162 3 3 6-7m6 4h10"
          fill="none"
          stroke={color}
          strokeWidth="1.4"
          strokeLinecap="round"
          pathLength="1"
          strokeDasharray="1"
          {...part('catalog-synced')}
        />
      </g>
    )
  if (kind === 3)
    return (
      <g className="agent-workstation" strokeLinejoin="round">
        <rect
          x="-18"
          y="113"
          width="41"
          height="58"
          rx="4"
          fill={paper}
          stroke={border}
        />
        <rect x="-7" y="110" width="19" height="7" rx="2" fill={ink} />
        <path
          d="M-9 126h23m-23 7h16m-9 12h16m-16 8h16m-16 8h12"
          stroke={border}
          strokeWidth="1.5"
          strokeLinecap="round"
        />
        <path
          d="m-10 143 2 2 4-5m-6 13 2 2 4-5"
          fill="none"
          stroke={color}
          strokeWidth="1.3"
        />
        <rect
          x="137"
          y="108"
          width="43"
          height="23"
          rx="4"
          fill={paper}
          stroke={border}
        />
        <path
          d="M145 121h27m-26-6h14"
          stroke={ink}
          strokeWidth="2"
          strokeLinecap="round"
        />
        <path d="m64 141 34-14 36 14-34 15Z" fill="#FFC48E" stroke="#D18D61" />
        <path d="m64 141 36 15v24l-36-15Z" fill="#F1A76E" stroke="#D18D61" />
        <path d="m100 156 34-15v24l-34 15Z" fill="#E78C58" stroke="#D18D61" />
        <path d="m79 135 36 15v10l-12 5v-10l-36-15Z" fill="#FFE0B9" />
        <g {...part('parcel-label')}>
          <rect
            x="-12"
            y="2"
            width="25"
            height="14"
            rx="2"
            fill={paper}
            stroke="#D7E0E9"
          />
          <path
            d="M-8 6h9m-9 3h16m-15 3h2m2 0h1m2 0h2m2 0h3"
            stroke={ink}
            strokeWidth="1"
          />
        </g>
        <path
          d="m-9 161 2 2 5-6"
          fill="none"
          stroke={color}
          strokeWidth="1.8"
          strokeLinecap="round"
          pathLength="1"
          strokeDasharray="1"
          {...part('order-check')}
        />
      </g>
    )
  if (kind === 4)
    return (
      <g className="agent-workstation" strokeLinejoin="round">
        <path
          d="M17 126h82l12 12v41H17Z"
          fill={paper}
          stroke={border}
          strokeWidth="1.2"
        />
        <path d="M99 126v12h12" fill="#EAF0FF" stroke={border} />
        <path
          d="M28 136h27m-27 7h17m17-7h24m-24 7h34M28 163h70m-70 7h46"
          stroke={border}
          strokeWidth="1.3"
          strokeLinecap="round"
        />
        <path
          d={invoiceTrace}
          fill="none"
          stroke={color}
          strokeWidth="1.6"
          strokeLinecap="round"
          pathLength="1"
          strokeDasharray="1"
          {...part('invoice-ink')}
        />
        <path
          d="m83 171 3 3 7-8"
          fill="none"
          stroke={color}
          strokeWidth="1.6"
          strokeLinecap="round"
          pathLength="1"
          strokeDasharray="1"
          {...part('invoice-check')}
        />
        <g {...part('invoice-pen')}>
          <path d="m-2 4 8-26 5 2L3 6Z" fill={ink} />
          <path d="m6-22 1-4a2.5 2.5 0 0 1 5 2l-1 4Z" fill={accent} />
          <path d="m-2 4 5 2-7 5Z" fill="#D6B18B" />
          <path d="m-3 8-1 3 3-2Z" fill={ink} />
        </g>
      </g>
    )
  return (
    <g className="agent-workstation" strokeLinejoin="round">
      <rect
        x="132"
        y="109"
        width="48"
        height="64"
        rx="5"
        fill={paper}
        stroke={border}
        strokeWidth="1.2"
      />
      <path d="M132 120h48" stroke={border} />
      <circle cx="138" cy="115" r="1.5" fill={accent} />
      <path
        d="M144 115h27"
        stroke={border}
        strokeWidth="2"
        strokeLinecap="round"
      />
      <rect
        x="139"
        y="127"
        width="33"
        height="8"
        rx="2"
        fill={accent}
        {...part('research-highlight')}
      />
      <path
        d="M139 130h27m-27 9h32m-32 6h24m-24 7h30m-30 7h21m-21 7h30"
        stroke={border}
        strokeWidth="1.5"
        strokeLinecap="round"
      />
      <rect
        x="-13"
        y="133"
        width="43"
        height="42"
        rx="3"
        fill={paper}
        stroke={border}
      />
      <path
        d="M-5 141h26m-26 7h17"
        stroke={border}
        strokeWidth="1.5"
        strokeLinecap="round"
      />
      <path
        d="M-5 156h25m-25 7h17"
        stroke={color}
        strokeWidth="1.6"
        strokeLinecap="round"
        pathLength="1"
        strokeDasharray="1"
        {...part('research-notes')}
      />
      <g {...part('research-lens')}>
        <path
          d="M0 0-7-8"
          stroke={ink}
          strokeWidth="4.5"
          strokeLinecap="round"
        />
        <circle
          cx="-13"
          cy="-15"
          r="10"
          fill="#E5F7FF"
          fillOpacity=".55"
          stroke={ink}
          strokeWidth="2.7"
        />
        <path
          d="M-18-18a5 5 0 0 1 5-3"
          fill="none"
          stroke={paper}
          strokeWidth="1.8"
          strokeLinecap="round"
        />
      </g>
    </g>
  )
}
