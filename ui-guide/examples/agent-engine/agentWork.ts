import type { Point } from './agentDeformation'

export const agentActivities = [
  { key: 'sorting-mail', label: 'SORTING THE INBOX' },
  { key: 'typing-replies', label: 'PREPARING A REPLY' },
  { key: 'updating-catalog', label: 'UPDATING THE CATALOG' },
  { key: 'labeling-orders', label: 'PREPARING AN ORDER' },
  { key: 'writing-invoices', label: 'REVIEWING AN INVOICE' },
  { key: 'reviewing-sources', label: 'REVIEWING SOURCES' },
]

// These curves route working arms around the body toward their workstation.
const workingArms: Record<number, string>[] = [
  { 0: 'M40 100C-18 104 -16 145 12 147', 1: 'M120 92C167 92 173 142 96 149' },
  { 0: 'M36 96C-6 120 15 150 51 155', 1: 'M126 105C130 139 126 157 98 155' },
  { 0: 'M31 110C-5 117 -8 153 14 147', 1: 'M124 109C160 105 172 135 153 150' },
  { 1: 'M37 82C-5 104 -16 145 3 145', 2: 'M110 117C150 113 140 142 104 151' },
  { 0: 'M53 45C18 28 -5 115 25 145', 1: 'M113 99C154 97 143 138 59 143' },
  { 2: 'M36 116C9 120 -3 144 15 150', 3: 'M122 119C155 110 189 131 165 153' },
]

export function workingArmPath(kind: number, index: number, fallback: string) {
  return workingArms[kind][index] ?? fallback
}

type PartPose = { transform?: string; opacity?: number; progress?: number }
export type WorkPose = {
  grips: Record<number, Point>
  parts: Record<string, PartPose>
}

const clamp = (value: number) => Math.max(0, Math.min(1, value))
const ease = (value: number, start: number, end: number) => {
  const x = clamp((value - start) / (end - start))
  return x * x * (3 - 2 * x)
}
const move = (x: number, y: number) =>
  `translate(${x.toFixed(2)} ${y.toFixed(2)})`

const writingSamples = Array.from({ length: 65 }, (_, index) => ({
  x: Number((45 + (index / 64) * 55).toFixed(2)),
  y: Number((154 + Math.sin((index / 64) * Math.PI * 8) * 1.7).toFixed(2)),
}))
const writingDistances = [0]
writingSamples.slice(1).forEach((point, index) => {
  const previous = writingSamples[index]
  writingDistances.push(
    writingDistances[index] +
      Math.hypot(point.x - previous.x, point.y - previous.y)
  )
})

// Match the pen position to the length of the visible ink, including its curves.
export function writingPoint(progress: number): Point {
  const distance =
    clamp(progress) * writingDistances[writingDistances.length - 1]
  let index = 1
  while (
    index < writingSamples.length - 1 &&
    writingDistances[index] < distance
  )
    index += 1
  const amount =
    (distance - writingDistances[index - 1]) /
    (writingDistances[index] - writingDistances[index - 1])
  const previous = writingSamples[index - 1]
  const next = writingSamples[index]
  return {
    x: previous.x + (next.x - previous.x) * amount,
    y: previous.y + (next.y - previous.y) * amount,
  }
}

export const invoiceTrace = writingSamples
  .map((point, index) => `${index ? 'L' : 'M'}${point.x} ${point.y}`)
  .join('')

export function getWorkPose(kind: number, time: number | null): WorkPose {
  const seconds = time ?? 2.4
  const phase = time === null ? 0.4 : (seconds % 6) / 6
  const tap = (offset = 0) => Math.max(0, Math.sin(seconds * 8 + offset))

  if (kind === 0) {
    const transfer = ease(phase, 0.15, 0.7)
    const release = ease(phase, 0.83, 1)
    const x = (18 + 150 * transfer) * (1 - release) + 18 * release
    const y =
      (142 + 9 * transfer - Math.sin(transfer * Math.PI) * 4) * (1 - release) +
      142 * release -
      Math.sin(release * Math.PI) * 8
    return {
      grips: { 0: { x: 11, y: 144 + tap() * 2 }, 1: { x, y } },
      parts: {
        'mail-transfer': {
          transform: move(x, y),
          opacity: 1 - ease(phase, 0.76, 0.83),
        },
        'mail-sorted': {
          opacity: ease(phase, 0.72, 0.82) * (1 - ease(phase, 0.95, 1)),
        },
      },
    }
  }
  if (kind === 1) {
    return {
      grips: {
        0: { x: 51 + Math.sin(seconds * 2.4) * 3, y: 150 + tap() * 5 },
        1: {
          x: 98 + Math.sin(seconds * 2.4 + 1.8) * 3,
          y: 150 + tap(Math.PI) * 5,
        },
      },
      parts: {
        'key-left': { opacity: 0.12 + tap() * 0.88 },
        'key-right': { opacity: 0.12 + tap(Math.PI) * 0.88 },
        'reply-line': { progress: ease(phase, 0.08, 0.7) },
        'reply-ready': { opacity: ease(phase, 0.7, 0.85) },
      },
    }
  }
  if (kind === 2) {
    return {
      grips: {
        0: { x: 14, y: 145 + tap() * 3 },
        1: { x: 154, y: 147 + tap(2.2) * 3 },
      },
      parts: {
        'catalog-button': { opacity: 0.2 + tap() * 0.8 },
        'stock-button': { opacity: 0.2 + tap(2.2) * 0.8 },
        'catalog-synced': { progress: ease(phase, 0.12, 0.75) },
      },
    }
  }
  if (kind === 3) {
    const press = ease(phase, 0.12, 0.65)
    const back = ease(phase, 0.82, 1)
    const label = {
      x: 151 - press * 47,
      y: 127 + press * 24 - Math.sin(press * Math.PI) * 7,
    }
    const hand = {
      x: label.x * (1 - back) + 151 * back,
      y: label.y * (1 - back) + 127 * back - Math.sin(back * Math.PI) * 9,
    }
    return {
      grips: { 1: { x: 3, y: 145 + tap() }, 2: hand },
      parts: {
        'parcel-label': {
          transform: move(label.x, label.y),
          opacity: ease(phase, 0.02, 0.1),
        },
        'order-check': { progress: ease(phase, 0.67, 0.82) },
      },
    }
  }
  if (kind === 4) {
    const progress = ease(phase, 0.12, 0.78)
    const back = ease(phase, 0.87, 1)
    const point = writingPoint(progress * (1 - back))
    const grip = {
      x: point.x + 4,
      y: point.y - 11 - Math.sin(back * Math.PI) * 13,
    }
    return {
      grips: { 0: { x: 25, y: 145 }, 1: grip },
      parts: {
        'invoice-pen': { transform: move(grip.x, grip.y) },
        'invoice-ink': { progress },
        'invoice-check': { progress: ease(phase, 0.8, 0.9) },
      },
    }
  }
  const scan = {
    x: 166 + Math.sin(seconds * 1.2) * 7,
    y: 151 + Math.sin(seconds * 1.8) * 5,
  }
  return {
    grips: { 2: { x: 15, y: 149 + tap() }, 3: scan },
    parts: {
      'research-lens': { transform: move(scan.x, scan.y) },
      'research-notes': { progress: ease(phase, 0.08, 0.85) },
      'research-highlight': {
        opacity: 0.3 + (Math.sin(seconds * 1.8) + 1) * 0.25,
      },
    },
  }
}
