// Draft generator for the five new FourStacks-style agent avatars (assistant, hr, email,
// subscriptions, bookings). Usage: node draft-new-avatars.mjs OUTPUT_DIRECTORY
// Kept so the shapes can be reproduced; export-avatars.mjs does not know these profiles yet.
// Mirrors the structure of ui-guide/assets/agents/*.svg: spectrum body, shade overlay,
// accent marking, tapered tendrils, white iris in a dark well.
import fs from 'node:fs'

const SHADE_PATH = 'M112 4C148 66 118 124 42 143L160 160L160 0Z'

export const profiles = {
  assistant: {
    title: 'Workspace assistant', silhouette: 'beacon',
    color: '#4666F0', shade: '#2A3FBF', accent: '#A9C4FF', backdrop: '#EEF1FF',
    spectrum: ['#9AAEFF', '#4A66F2', '#2A3FBF'],
    eyes: [{ x: 78, y: 74, radius: 5.4 }, { x: 84, y: 103, radius: 8.6 }],
    tendrils: ['M34 100C10 104 18 128 6 134', 'M130 104C150 98 146 124 156 128'],
    marking: 'M34 118C60 130 104 128 130 108L128 122C104 142 58 144 36 130Z',
    path: 'M96 6C98 22 92 34 104 48C124 66 138 84 130 108C124 126 108 128 98 140C88 152 66 152 52 140C36 128 26 108 32 88C38 68 56 62 66 48C76 34 74 20 84 14C88 11 92 8 96 6Z',
  },
  hr: {
    title: 'HR assistant', silhouette: 'clover',
    color: '#2B6CB0', shade: '#1A3F78', accent: '#8FD0F0', backdrop: '#EBF4FB',
    spectrum: ['#7FC3F2', '#3584D6', '#21509F'],
    eyes: [{ x: 63, y: 84, radius: 6 }, { x: 97, y: 84, radius: 6 }],
    tendrils: ['M44 128C28 144 40 152 24 156', 'M118 128C134 142 122 152 138 156'],
    marking: 'M28 88C56 100 104 100 132 88L130 100C102 112 58 112 30 100Z',
    path: 'M80 22C94 10 114 16 116 34C118 40 120 44 126 46C146 50 148 72 134 82C128 86 128 92 134 98C148 110 142 132 122 134C114 135 112 138 110 146C104 160 84 156 80 144C76 156 56 160 50 146C48 138 46 135 38 134C18 132 12 110 26 98C32 92 32 86 26 82C12 72 14 50 34 46C40 44 42 40 44 34C46 16 66 10 80 22Z',
  },
  email: {
    title: 'Email assistant', silhouette: 'swallow',
    color: '#1A7AD0', shade: '#1B4C9A', accent: '#9FDCFF', backdrop: '#EAF5FE',
    spectrum: ['#7AD0FF', '#2A8DE6', '#2A55BD'],
    eyes: [{ x: 58, y: 76, radius: 4.6 }, { x: 80, y: 68, radius: 5.6 }, { x: 102, y: 76, radius: 4.6 }],
    tendrils: ['M40 124C28 142 40 150 26 156', 'M120 124C132 142 120 150 134 156'],
    marking: 'M26 78L80 124L134 78L134 90L80 138L26 90Z',
    path: 'M80 40C96 36 106 24 124 18C142 12 154 22 150 38C148 50 138 56 130 66C124 74 128 84 134 92C142 102 150 108 148 122C146 138 124 136 112 126C100 118 92 120 80 134C68 120 60 118 48 126C36 136 14 138 12 122C10 108 18 102 26 92C32 84 36 74 30 66C22 56 12 50 10 38C6 22 18 12 36 18C54 24 64 36 80 40Z',
  },
  subscriptions: {
    title: 'Subscriptions agent', silhouette: 'loop',
    color: '#2A52D6', shade: '#162E85', accent: '#94B8FF', backdrop: '#EDF1FD',
    spectrum: ['#7FA2FF', '#2F58E0', '#1A35A3'],
    eyes: [{ x: 46, y: 66, radius: 5.2 }, { x: 50, y: 100, radius: 7.4 }],
    tendrils: ['M112 34C132 28 134 48 150 44', 'M132 124C146 120 150 138 156 144'],
    marking: 'M22 96C30 122 56 138 92 138L94 146C54 150 24 126 20 96Z',
    path: 'M96 14C60 8 22 38 20 80C18 120 54 152 94 146C112 144 124 136 134 124C108 134 78 122 72 94C66 68 84 40 112 34C108 24 104 16 96 14Z',
  },
  bookings: {
    title: 'Bookings agent', silhouette: 'tabs',
    color: '#1D6FD0', shade: '#174A96', accent: '#8AD6FF', backdrop: '#EAF4FF',
    spectrum: ['#74C8FF', '#2A86E8', '#2653BC'],
    eyes: [{ x: 62, y: 90, radius: 7.4 }, { x: 100, y: 90, radius: 7.4 }],
    tendrils: ['M32 104C12 108 20 130 6 132', 'M130 108C150 104 146 128 156 130'],
    marking: 'M30 56C60 64 104 64 132 56L132 68C104 76 60 76 30 68Z',
    path: 'M28 50C26 38 38 32 48 36C50 24 52 10 64 12C74 14 68 30 76 34C84 36 92 34 96 30C100 18 104 8 116 14C124 20 114 30 118 36C130 36 138 46 134 58C132 78 140 98 132 118C126 134 110 140 96 134C84 130 76 146 60 142C42 138 28 130 26 112C24 92 30 70 28 50Z',
  },
}

function cubicPoints(d, steps = 14) {
  const n = d.match(/-?\d+(?:\.\d+)?/g).map(Number)
  let [x0, y0] = n; const pts = [[x0, y0]]
  for (let i = 2; i + 5 < n.length + 0; i += 6) {
    const [x1, y1, x2, y2, x3, y3] = n.slice(i, i + 6)
    for (let s = 1; s <= steps; s++) {
      const t = s / steps, u = 1 - t
      pts.push([
        u ** 3 * x0 + 3 * u * u * t * x1 + 3 * u * t * t * x2 + t ** 3 * x3,
        u ** 3 * y0 + 3 * u * u * t * y1 + 3 * u * t * t * y2 + t ** 3 * y3,
      ])
    }
    x0 = x3; y0 = y3
  }
  return pts
}

function taperedPath(d) {
  const pts = cubicPoints(d)
  const left = [], right = []
  pts.forEach((p, i) => {
    const a = pts[Math.max(0, i - 1)], b = pts[Math.min(pts.length - 1, i + 1)]
    const dx = b[0] - a[0], dy = b[1] - a[1], len = Math.hypot(dx, dy) || 1
    const w = (8.2 - 7.0 * (i / (pts.length - 1))) / 2
    left.push([p[0] - (dy / len) * w, p[1] + (dx / len) * w])
    right.push([p[0] + (dy / len) * w, p[1] - (dx / len) * w])
  })
  const f = (q) => `${q[0].toFixed(2)} ${q[1].toFixed(2)}`
  return `M${f(left[0])}L${left.slice(1).map(f).join('L')}L${right.reverse().map(f).join('L')}Z`
}

export function render(id, p, { size = 160 } = {}) {
  const tendrils = p.tendrils.map((d, i) =>
    `<path d="${taperedPath(d)}" fill="${i % 2 ? p.accent : p.shade}"/>`).join('')
  const eyes = p.eyes.map((e) =>
    `<g transform="translate(${e.x} ${e.y})"><circle r="${(e.radius * 1.992).toFixed(2)}" fill="#15203A"/>` +
    `<circle r="${(e.radius * 1.38).toFixed(2)}" fill="${p.accent}" opacity=".45"/>` +
    `<circle r="${e.radius}" fill="#FFFFFF"/></g>`).join('')
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="-8 -8 176 176" width="${size}" height="${size}" role="img" aria-labelledby="${id}-t"><title id="${id}-t">${p.title} avatar (draft)</title>` +
    `<defs><linearGradient id="${id}-g" x1="12%" y1="0%" x2="87%" y2="100%">` +
    p.spectrum.map((c, i) => `<stop offset="${i * 50}%" stop-color="${c}"/>`).join('') +
    `</linearGradient><clipPath id="${id}-c"><path d="${p.path}"/></clipPath></defs>` +
    `<g>${tendrils}</g><path d="${p.path}" fill="url(#${id}-g)"/>` +
    `<path d="${SHADE_PATH}" fill="${p.shade}" opacity=".38" clip-path="url(#${id}-c)"/>` +
    `<path d="${p.marking}" fill="${p.accent}" opacity=".74" clip-path="url(#${id}-c)"/>` +
    `<g>${eyes}</g></svg>`
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const out = process.argv[2]
  fs.mkdirSync(out, { recursive: true })
  for (const [id, p] of Object.entries(profiles)) fs.writeFileSync(`${out}/${id}.svg`, render(id, p))
  console.log('wrote', Object.keys(profiles).join(', '))
}
