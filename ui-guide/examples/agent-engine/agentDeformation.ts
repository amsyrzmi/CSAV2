export type Point = { x: number; y: number }
type Warp = (x: number, y: number) => Point
type Contour = { parts: string[]; points: Point[] }
type Tendril = { points: Point[]; radius: number }

const numbers = /-?\d*\.?\d+/g
const identity: Warp = (x, y) => ({ x, y })
const pointText = ({ x, y }: Point) => `${x.toFixed(2)} ${y.toFixed(2)}`

// The artwork uses absolute M/L/C commands. One continuous field deforms
// its anchors and control points, keeping the body and its markings together.
export function prepareContour(path: string): Contour {
  const values = path.match(numbers)!.map(Number)
  const points = []
  for (let i = 0; i < values.length; i += 2)
    points.push({ x: values[i], y: values[i + 1] })
  return { parts: path.split(numbers), points }
}

export function deformContour(contour: Contour, warp: Warp) {
  return contour.points.reduce((path, point, index) => {
    const moved = warp(point.x, point.y)
    return (
      path +
      moved.x.toFixed(2) +
      contour.parts[index * 2 + 1] +
      moved.y.toFixed(2) +
      contour.parts[index * 2 + 2]
    )
  }, contour.parts[0])
}

export function createBodyWarp(time: number, kind: number, pull: Point): Warp {
  const phase = time * 0.95 + kind * 0.71
  const breath = Math.sin(phase) * 0.047 + Math.sin(phase * 1.63) * 0.012
  const width = 1 + breath + Math.abs(pull.x) * 0.025 - pull.y * 0.025
  const height = 1 - breath * 0.83 - Math.abs(pull.x) * 0.018 + pull.y * 0.025

  return (x, y) => {
    const dx = x - 80
    const dy = y - 84
    const edge = 0.3 + Math.min(1, Math.hypot(dx / 80, dy / 84)) * 0.7
    return {
      x:
        80 +
        dx * width +
        Math.sin(dy * 0.048 - phase * 1.3) * 3.8 * edge +
        Math.sin(dx * 0.03 + dy * 0.02 + phase) * 1.5 +
        pull.x * (3 + dy * 0.035),
      y:
        84 +
        dy * height +
        Math.sin(dx * 0.043 + phase * 1.1) * 3.2 * edge +
        Math.sin(phase * 0.72) * 1.5 +
        pull.y * 2.8,
    }
  }
}

export function prepareTendril(path: string, radius: number): Tendril {
  return { points: prepareContour(path).points, radius }
}

function curvePoint(points: Point[], position: number): Point {
  const [a, b, c, d] = points
  const inverse = 1 - position
  return {
    x:
      inverse ** 3 * a.x +
      3 * inverse ** 2 * position * b.x +
      3 * inverse * position ** 2 * c.x +
      position ** 3 * d.x,
    y:
      inverse ** 3 * a.y +
      3 * inverse ** 2 * position * b.y +
      3 * inverse * position ** 2 * c.y +
      position ** 3 * d.y,
  }
}

function smoothOutline(points: Point[]) {
  const length = points.length
  let path = `M${pointText(points[0])}`
  for (let i = 0; i < length; i++) {
    const previous = points[(i + length - 1) % length]
    const current = points[i]
    const next = points[(i + 1) % length]
    const following = points[(i + 2) % length]
    path += `C${pointText({
      x: current.x + (next.x - previous.x) / 6,
      y: current.y + (next.y - previous.y) / 6,
    })} ${pointText({
      x: next.x - (following.x - current.x) / 6,
      y: next.y - (following.y - current.y) / 6,
    })} ${pointText(next)}`
  }
  return `${path}Z`
}

export function deformTendril(
  tendril: Tendril,
  warp: Warp = identity,
  time: number | null = null,
  phase = 0,
  grip?: Point
) {
  const root = tendril.points[0]
  const centers: Point[] = []
  const samples = 10
  for (let i = 0; i <= samples; i++) {
    const position = i / samples
    const point = curvePoint(tendril.points, position)
    const before = curvePoint(tendril.points, Math.max(0, position - 0.01))
    const after = curvePoint(tendril.points, Math.min(1, position + 0.01))
    const dx = after.x - before.x
    const dy = after.y - before.y
    const distance = Math.hypot(dx, dy) || 1
    const wave =
      time === null
        ? 0
        : Math.sin(time * 1.65 - position * 4.6 + phase) * 9 * position ** 1.25
    const stretch = time === null ? 1 : 1 + Math.sin(time * 1.1 + phase) * 0.065
    centers.push(
      warp(
        root.x + (point.x - root.x) * stretch - (dy / distance) * wave,
        root.y + (point.y - root.y) * stretch + (dx / distance) * wave
      )
    )
  }

  // Keep a working hand on its tool while the rest of the arm stays soft.
  if (grip) {
    const tip = centers[samples]
    const offset = { x: grip.x - tip.x, y: grip.y - tip.y }
    centers.forEach((point, index) => {
      const position = index / samples
      const weight = position * position * (3 - 2 * position)
      point.x += offset.x * weight
      point.y += offset.y * weight
    })
  }

  const left: Point[] = []
  const right: Point[] = []
  let tipCap = centers[samples]
  for (let i = 0; i <= samples; i++) {
    const before = centers[Math.max(0, i - 1)]
    const after = centers[Math.min(samples, i + 1)]
    const dx = after.x - before.x
    const dy = after.y - before.y
    const distance = Math.hypot(dx, dy) || 1
    const position = i / samples
    const pulse =
      time === null
        ? 1
        : 1 + Math.sin(time * 1.65 - position * 3 + phase) * 0.07
    const radius = tendril.radius * (1 - position * 0.5) * pulse
    left.push({
      x: centers[i].x - (dy / distance) * radius,
      y: centers[i].y + (dx / distance) * radius,
    })
    right.push({
      x: centers[i].x + (dy / distance) * radius,
      y: centers[i].y - (dx / distance) * radius,
    })
    if (i === samples)
      tipCap = {
        x: centers[i].x + (dx / distance) * radius,
        y: centers[i].y + (dy / distance) * radius,
      }
  }
  return {
    path: smoothOutline([...left, tipCap, ...right.reverse()]),
    tip: centers[samples],
  }
}
