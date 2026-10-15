import { useEffect, useId, useMemo, useRef } from 'react'
import AgentFace from './AgentFace'
import AgentWorkstation from './AgentWorkstation'
import { agentActivities, getWorkPose, workingArmPath } from './agentWork'
import {
  createBodyWarp,
  deformContour,
  deformTendril,
  prepareContour,
  prepareTendril,
} from './agentDeformation'

export const blobProfiles = [
  {
    silhouette: 'glider',
    color: '#0875CA',
    shade: '#1C4AAC',
    accent: '#81D7FF',
    backdrop: '#EAF6FF',
    spectrum: ['#67D8FF', '#169FEB', '#3264FF'],
    eyes: [
      { x: 65, y: 81, radius: 5.2 },
      { x: 94, y: 81, radius: 5.2 },
    ],
    tendrils: [
      'M40 100C26 112 14 110 10 121',
      'M120 92C135 92 141 104 151 100',
    ],
    marking: 'M19 64C36 71 43 74 54 72L49 81C36 82 26 76 15 73Z',
    path: 'M80 34C96 34 104 55 119 54C134 53 139 34 150 40C163 50 144 73 137 84C128 99 136 119 121 128C106 138 93 116 80 122C66 128 54 147 40 138C26 130 35 107 27 94C19 81 2 75 6 59C11 45 30 59 42 57C58 55 61 34 80 34Z',
  },
  {
    silhouette: 'crowned',
    color: '#3264FF',
    shade: '#183DA5',
    accent: '#7BC9FF',
    backdrop: '#EDF2FF',
    spectrum: ['#78ACFF', '#3264FF', '#2349CA'],
    eyes: [{ x: 80, y: 88, radius: 12.5 }],
    tendrils: [
      'M36 96C8 99 24 54 5 64',
      'M126 105C151 118 139 143 155 135',
      'M72 132C56 145 31 142 27 153',
    ],
    marking: 'M36 108C56 125 107 130 129 103L133 120C103 146 55 143 35 122Z',
    path: 'M54 45C38 31 38 13 48 12C61 12 59 34 67 35C71 35 67 6 81 6C96 6 91 37 100 37C108 37 114 13 126 19C140 27 121 49 119 61C144 78 146 106 129 126C116 144 95 145 78 140C58 150 34 139 27 121C18 103 17 79 35 64C37 59 46 53 54 45Z',
  },
  {
    silhouette: 'cluster',
    color: '#126BB3',
    shade: '#164B8D',
    accent: '#73CBF2',
    backdrop: '#EBF5FC',
    spectrum: ['#6FCAFA', '#218FDE', '#2253B1'],
    eyes: [
      { x: 54, y: 77, radius: 5.2 },
      { x: 82, y: 66, radius: 6.4 },
      { x: 108, y: 79, radius: 5.2 },
    ],
    tendrils: [
      'M31 110C16 117 12 135 5 130',
      'M124 109C140 121 151 110 156 123',
    ],
    marking: 'M30 106C55 115 106 106 137 112L140 128C99 119 65 139 31 121Z',
    path: 'M29 68C15 54 20 31 36 28C48 25 54 41 60 42C66 43 64 16 80 15C99 14 91 44 102 44C111 43 115 27 129 32C146 38 139 60 133 71C148 82 151 104 138 118C127 130 113 121 103 130C90 143 78 151 63 137C50 126 39 139 26 128C9 113 11 82 29 68Z',
  },
  {
    silhouette: 'comet',
    color: '#2457E5',
    shade: '#19378F',
    accent: '#9ABFFF',
    backdrop: '#EEF2FC',
    spectrum: ['#80ADFF', '#3968F2', '#153EA7'],
    eyes: [
      { x: 70, y: 81, radius: 7.6 },
      { x: 103, y: 75, radius: 4.8 },
    ],
    tendrils: [
      'M48 57C29 59 28 29 8 36',
      'M37 82C15 82 24 110 4 103',
      'M110 117C130 138 147 124 153 140',
    ],
    marking: 'M31 49C55 50 78 41 94 49L88 61C69 57 53 68 36 63Z',
    path: 'M37 46C49 34 68 32 84 40C104 47 119 33 133 42C146 52 132 66 133 76C133 90 153 91 151 106C149 122 128 114 119 121C112 127 122 144 108 150C91 157 87 135 75 133C62 132 48 148 35 139C23 129 43 113 36 103C27 91 7 91 9 76C10 62 25 60 37 46Z',
  },
  {
    silhouette: 'ribbon',
    color: '#2D64D9',
    shade: '#193F9B',
    accent: '#A3D9FF',
    backdrop: '#ECF3FF',
    spectrum: ['#8ACBFF', '#3C8AF3', '#2850CE'],
    eyes: [
      { x: 67, y: 64, radius: 4.8 },
      { x: 92, y: 61, radius: 4.8 },
      { x: 67, y: 89, radius: 4.8 },
      { x: 92, y: 86, radius: 4.8 },
    ],
    tendrils: ['M53 45C24 34 28 64 9 58', 'M113 99C143 86 127 130 151 125'],
    marking: 'M31 111L125 97L130 108L37 124ZM39 32L111 19L113 28L40 42Z',
    path: 'M65 9C82 2 106 14 105 33C105 44 126 46 124 62C123 76 109 78 112 91C115 103 131 109 124 123C118 137 100 127 92 138C80 154 61 158 49 144C38 131 53 120 47 109C41 98 27 94 34 78C39 66 54 69 52 55C51 42 34 37 41 22C46 11 54 13 65 9Z',
  },
  {
    silhouette: 'scout',
    color: '#2466C1',
    shade: '#1B3C87',
    accent: '#75B9FF',
    backdrop: '#EDF4FE',
    spectrum: ['#6ABBF4', '#2D7DDF', '#244BA7'],
    eyes: [
      { x: 82, y: 91, radius: 9.5 },
      { x: 32, y: 26, radius: 4 },
      { x: 128, y: 17, radius: 4 },
    ],
    tendrils: [
      'M52 65C53 40 30 44 32 26',
      'M110 64C144 53 132 27 128 17',
      'M36 116C8 109 20 151 5 143',
      'M122 119C145 127 134 150 155 147',
    ],
    marking: 'M21 78C41 63 102 60 140 79L137 91C91 75 52 82 24 93Z',
    path: 'M63 47C56 33 65 21 79 23C96 24 96 46 107 49C119 51 128 39 137 47C151 59 130 73 137 87C143 101 153 111 141 122C130 132 118 118 108 128C98 138 107 151 91 155C77 159 75 138 62 136C47 134 39 145 27 135C12 123 30 112 26 99C23 85 7 79 15 65C23 51 42 63 49 56C52 54 58 51 63 47Z',
  },
]

const shadePath = 'M112 4C148 66 118 124 42 143L160 160L160 0Z'

export default function BlobAgent({
  kind = 1,
  x = 0,
  y = 0,
  size = 160,
  paused = false,
}: {
  kind?: number
  x?: number
  y?: number
  size?: number
  paused?: boolean
}) {
  const profile = blobProfiles[kind]
  const id = `blob-${useId().replace(/:/g, '')}`
  const root = useRef<SVGGElement>(null)
  const surface = useRef<SVGPathElement>(null)
  const outline = useRef<SVGPathElement>(null)
  const shade = useRef<SVGPathElement>(null)
  const marking = useRef<SVGPathElement>(null)
  const tendrils = useRef<(SVGPathElement | null)[]>([])
  const elapsed = useRef(kind * 630)
  const pull = useRef({ x: 0, y: 0, vx: 0, vy: 0 })
  const geometry = useMemo(() => {
    const restWork = getWorkPose(kind, null)
    return {
      restWork,
      body: prepareContour(profile.path),
      shade: prepareContour(shadePath),
      marking: prepareContour(profile.marking),
      tendrils: profile.tendrils.map((path, index) => {
        const curve = prepareTendril(
          workingArmPath(kind, index, path),
          index % 2 ? 4.5 : 5.5
        )
        return {
          curve,
          rest: deformTendril(curve, undefined, null, 0, restWork.grips[index]),
        }
      }),
    }
  }, [kind, profile])

  useEffect(() => {
    const element = root.current
    if (!element) return
    const preference = window.matchMedia('(prefers-reduced-motion: reduce)')
    let visible = false
    let frame: number | null = null
    let previous: number | null = null
    let lastPaint = 0
    const target = { x: 0, y: 0 }
    const eyes = Array.from(element.querySelectorAll<SVGGElement>('.agent-eye'))
    const interactive = element.closest('[data-agent-interactive]')
    const workParts = new Map(
      Array.from(element.querySelectorAll<SVGElement>('[data-work-part]')).map(
        (node) => [node.getAttribute('data-work-part')!, node]
      )
    )

    const draw = (time: number | null) => {
      const work = getWorkPose(kind, time)
      const warp = createBodyWarp(time ?? 0, kind, pull.current)
      const path =
        time === null ? profile.path : deformContour(geometry.body, warp)
      surface.current?.setAttribute('d', path)
      outline.current?.setAttribute('d', path)
      shade.current?.setAttribute(
        'd',
        time === null ? shadePath : deformContour(geometry.shade, warp)
      )
      marking.current?.setAttribute(
        'd',
        time === null ? profile.marking : deformContour(geometry.marking, warp)
      )
      const tips = geometry.tendrils.map(({ curve, rest }, index) => {
        const pose =
          time === null
            ? rest
            : deformTendril(
                curve,
                warp,
                time,
                kind * 0.8 + index * 1.7,
                work.grips[index]
              )
        tendrils.current[index]?.setAttribute('d', pose.path)
        return pose.tip
      })
      Object.entries(work.parts).forEach(([name, pose]) => {
        const node = workParts.get(name)
        if (pose.transform !== undefined)
          node?.setAttribute('transform', pose.transform)
        if (pose.opacity !== undefined)
          node?.setAttribute('opacity', String(pose.opacity))
        if (pose.progress !== undefined)
          node?.setAttribute('stroke-dashoffset', String(1 - pose.progress))
      })
      profile.eyes.forEach((eye, index) => {
        // The scout's outer eyes sit at the ends of its flexible stalks.
        const position =
          time === null
            ? eye
            : profile.silhouette === 'scout' && index > 0
              ? tips[index - 1]
              : warp(eye.x, eye.y)
        eyes[index]?.setAttribute(
          'transform',
          `translate(${position.x.toFixed(2)} ${position.y.toFixed(2)})`
        )
      })
    }

    const paint = (time: number) => {
      const step = previous === null ? 0 : Math.min(time - previous, 40) / 1000
      elapsed.current += step * 1000
      previous = time
      const spring = pull.current
      spring.vx += ((target.x - spring.x) * 85 - spring.vx * 10) * step
      spring.vy += ((target.y - spring.y) * 85 - spring.vy * 10) * step
      spring.x += spring.vx * step
      spring.y += spring.vy * step
      if (time - lastPaint > 32) {
        draw(elapsed.current / 1000)
        lastPaint = time
      }
      frame = requestAnimationFrame(paint)
    }

    const release = () => {
      target.x = 0
      target.y = 0
    }
    const follow = (event: Event) => {
      const pointer = event as PointerEvent
      if (
        paused ||
        !visible ||
        document.hidden ||
        preference.matches ||
        pointer.pointerType === 'touch'
      )
        return
      const bounds = interactive?.getBoundingClientRect()
      if (!bounds?.width || !bounds.height) return
      target.x = Math.max(
        -1,
        Math.min(1, ((pointer.clientX - bounds.left) / bounds.width - 0.5) * 2)
      )
      target.y = Math.max(
        -1,
        Math.min(1, ((pointer.clientY - bounds.top) / bounds.height - 0.5) * 2)
      )
    }
    const sync = () => {
      const active =
        visible && !document.hidden && !paused && !preference.matches
      element.setAttribute(
        'data-motion-visible',
        String(visible && !document.hidden)
      )
      if (active && frame === null) {
        previous = null
        frame = requestAnimationFrame(paint)
      } else if (!active && frame !== null) {
        cancelAnimationFrame(frame)
        frame = null
      }
      if (!active) release()
      if (preference.matches) {
        pull.current = { x: 0, y: 0, vx: 0, vy: 0 }
        draw(null)
      }
    }
    const observer =
      'IntersectionObserver' in window
        ? new IntersectionObserver(
            ([entry]) => {
              visible = entry.isIntersecting
              sync()
            },
            { threshold: 0.01 }
          )
        : null
    if (observer) observer.observe(element)
    else visible = true
    document.addEventListener('visibilitychange', sync)
    preference.addEventListener('change', sync)
    interactive?.addEventListener('pointermove', follow, { passive: true })
    interactive?.addEventListener('pointerleave', release)
    interactive?.addEventListener('pointercancel', release)
    sync()
    return () => {
      if (frame !== null) cancelAnimationFrame(frame)
      observer?.disconnect()
      document.removeEventListener('visibilitychange', sync)
      preference.removeEventListener('change', sync)
      interactive?.removeEventListener('pointermove', follow)
      interactive?.removeEventListener('pointerleave', release)
      interactive?.removeEventListener('pointercancel', release)
    }
  }, [geometry, kind, paused, profile])

  return (
    <g
      transform={`translate(${x} ${y}) scale(${size / 160})`}
      className="blob-placement"
      data-blob-kind={kind}
      data-silhouette={profile.silhouette}
    >
      <g
        className="blob-agent"
        data-deformation="soft-body"
        data-agent-task={agentActivities[kind].key}
        ref={root}
      >
        <defs>
          <linearGradient
            id={`${id}-spectrum`}
            x1="12%"
            y1="0%"
            x2="87%"
            y2="100%"
          >
            {profile.spectrum.map((stop, index) => (
              <stop key={stop} offset={`${index * 50}%`} stopColor={stop} />
            ))}
          </linearGradient>
          <clipPath id={id}>
            <path ref={outline} d={profile.path} />
          </clipPath>
          <clipPath id={`${id}-work-front`}>
            <rect x="-40" y="125" width="260" height="95" />
          </clipPath>
        </defs>
        <g className="blob-tendrils">
          {geometry.tendrils.map(({ rest }, index) => (
            <path
              key={index}
              id={`${id}-hand-${index}`}
              ref={(element) => {
                tendrils.current[index] = element
              }}
              className="blob-tendril"
              d={rest.path}
              fill={index % 2 ? profile.accent : profile.shade}
            />
          ))}
        </g>
        <path
          className="blob-surface"
          ref={surface}
          d={profile.path}
          fill={`url(#${id}-spectrum)`}
        />
        <path
          className="blob-shade"
          ref={shade}
          d={shadePath}
          fill={profile.shade}
          opacity=".38"
          clipPath={`url(#${id})`}
        />
        <path
          className="blob-marking"
          ref={marking}
          d={profile.marking}
          fill={profile.accent}
          opacity=".74"
          clipPath={`url(#${id})`}
        />
        <AgentFace eyes={profile.eyes} accent={profile.accent} />
        <AgentWorkstation
          kind={kind}
          color={profile.color}
          accent={profile.accent}
        />
        <g className="agent-working-hands" clipPath={`url(#${id}-work-front)`}>
          {Object.keys(geometry.restWork.grips).map((index) => (
            <use key={index} href={`#${id}-hand-${index}`} />
          ))}
        </g>
      </g>
    </g>
  )
}
