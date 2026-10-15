import { useEffect, useRef } from 'react'
import type { PointerEvent } from 'react'

export default function useAgentGaze<T extends HTMLElement | SVGSVGElement>(
  paused: boolean
) {
  const ref = useRef<T>(null)
  const frame = useRef<number | null>(null)
  const position = useRef({ x: 0, y: 0 })

  function resetGaze() {
    if (frame.current !== null) cancelAnimationFrame(frame.current)
    frame.current = null
    ref.current?.style.removeProperty('--agent-look-x')
    ref.current?.style.removeProperty('--agent-look-y')
  }

  useEffect(() => {
    if (paused) resetGaze()
  }, [paused])

  useEffect(() => {
    const element = ref.current
    if (!element) return
    let inView = true
    const preference = window.matchMedia('(prefers-reduced-motion: reduce)')
    const syncActivity = () => {
      const visible = inView && !document.hidden
      element.setAttribute('data-motion-visible', String(visible))
      if (!visible || preference.matches) resetGaze()
    }
    const observer =
      'IntersectionObserver' in window
        ? new IntersectionObserver(
            ([entry]) => {
              inView = entry.isIntersecting
              syncActivity()
            },
            { threshold: 0.01 }
          )
        : null
    observer?.observe(element)
    document.addEventListener('visibilitychange', syncActivity)
    preference.addEventListener('change', syncActivity)
    syncActivity()
    return () => {
      observer?.disconnect()
      document.removeEventListener('visibilitychange', syncActivity)
      preference.removeEventListener('change', syncActivity)
    }
  }, [])

  useEffect(
    () => () => {
      if (frame.current !== null) cancelAnimationFrame(frame.current)
    },
    []
  )

  function onPointerMove(event: PointerEvent<T>) {
    if (
      paused ||
      event.pointerType === 'touch' ||
      window.matchMedia('(prefers-reduced-motion: reduce)').matches
    )
      return
    const bounds = event.currentTarget.getBoundingClientRect()
    if (!bounds.width || !bounds.height) return
    position.current = {
      x:
        Math.max(
          -1,
          Math.min(1, ((event.clientX - bounds.left) / bounds.width - 0.5) * 2)
        ) * 3,
      y:
        Math.max(
          -1,
          Math.min(1, ((event.clientY - bounds.top) / bounds.height - 0.5) * 2)
        ) * 2,
    }
    if (frame.current !== null) return
    frame.current = requestAnimationFrame(() => {
      frame.current = null
      ref.current?.style.setProperty(
        '--agent-look-x',
        `${position.current.x.toFixed(2)}px`
      )
      ref.current?.style.setProperty(
        '--agent-look-y',
        `${position.current.y.toFixed(2)}px`
      )
    })
  }

  return {
    ref,
    onPointerMove,
    onPointerLeave: resetGaze,
    'data-agent-interactive': 'true',
  }
}
