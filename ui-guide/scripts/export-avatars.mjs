// Run from the workspace with Node after installing FourStacks' dependencies.
// Exports the actual landing-page profiles; no independent redraws to maintain.
import { createRequire } from 'node:module'
import { copyFile, mkdir, writeFile } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const workspace = resolve(dirname(fileURLToPath(import.meta.url)), '../..')
const app = resolve(workspace, 'FourStacks')
const appRequire = createRequire(resolve(app, 'package.json'))
const { build } = appRequire('esbuild')
const output = resolve(workspace, 'ui-guide/assets/agents')
const entry = `
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import BlobAgent, { blobProfiles } from './src/components/BlobAgent'
import AgentFace from './src/components/AgentFace'
import StackMark from './src/components/StackMark'
import { prepareTendril, deformTendril } from './src/components/agentDeformation'
import { builtServices } from './src/data/site'

const shadePath = 'M112 4C148 66 118 124 42 143L160 160L160 0Z'
function Body({ kind, prefix }) {
  const p = blobProfiles[kind]
  return <g>
    <defs>
      <linearGradient id={prefix+'-spectrum'} x1="12%" y1="0%" x2="87%" y2="100%">
        {p.spectrum.map((color, i) => <stop key={color} offset={i*50+'%'} stopColor={color}/>)}
      </linearGradient>
      <clipPath id={prefix+'-clip'}><path d={p.path}/></clipPath>
    </defs>
    <g>{p.tendrils.map((path, i) => <path key={i} d={deformTendril(prepareTendril(path, i%2 ? 4.5 : 5.5)).path} fill={i%2 ? p.accent : p.shade}/>)}</g>
    <path d={p.path} fill={'url(#'+prefix+'-spectrum)'}/>
    <path d={shadePath} fill={p.shade} opacity=".38" clipPath={'url(#'+prefix+'-clip)'}/>
    <path d={p.marking} fill={p.accent} opacity=".74" clipPath={'url(#'+prefix+'-clip)'}/>
    <AgentFace eyes={p.eyes} accent={p.accent}/>
  </g>
}
export const avatars = builtServices.map(s => {
  const p = blobProfiles[s.artKind]
  const avatar = renderToStaticMarkup(<svg xmlns="http://www.w3.org/2000/svg" viewBox="-8 -8 176 176" width="160" height="160" role="img" aria-labelledby={s.id+'-title'}><title id={s.id+'-title'}>{s.title+' avatar'}</title><Body kind={s.artKind} prefix={s.id}/></svg>)
  const working = renderToStaticMarkup(<svg xmlns="http://www.w3.org/2000/svg" viewBox="-32 -8 224 208" width="224" height="208" role="img" aria-labelledby={s.id+'-working-title'}><title id={s.id+'-working-title'}>{s.title+' — static working illustration'}</title><BlobAgent kind={s.artKind} paused/></svg>, { identifierPrefix: s.id+'-working-' })
  return { id:s.id, title:s.title, artKind:s.artKind, silhouette:p.silhouette, eyeCount:p.eyes.length, color:p.color, shade:p.shade, accent:p.accent, backdrop:p.backdrop, spectrum:p.spectrum, avatar, working }
})
export const sheet = renderToStaticMarkup(<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1120 600" width="1120" height="600" role="img" aria-labelledby="sheet-title">
  <title id="sheet-title">FourStacks agent avatar family</title>
  <rect width="1120" height="600" fill="#F7F8FA"/>
  <text x="28" y="38" fill="#1C2B40" fontFamily="Inter, Arial, sans-serif" fontWeight="700" fontSize="21">FourStacks / Agent avatar family</text>
  <text x="28" y="62" fill="#566274" fontFamily="Inter, Arial, sans-serif" fontSize="12">Original blue vector silhouettes · white eyes · stable service identities</text>
  {builtServices.map((s,i) => {
    const p=blobProfiles[s.artKind], x=20+(i%3)*365, y=86+Math.floor(i/3)*250
    return <g key={s.id}>
      <rect x={x} y={y} width="350" height="232" rx="16" fill="#FFFFFF" stroke="#DCE2E9"/>
      <circle cx={x+175} cy={y+85} r="68" fill={p.backdrop}/>
      <svg x={x+105} y={y+13} width="140" height="140" viewBox="-8 -8 176 176"><Body kind={s.artKind} prefix={'sheet-'+s.id}/></svg>
      <text x={x+175} y={y+180} textAnchor="middle" fill="#1C2B40" fontFamily="Inter, Arial, sans-serif" fontSize="15" fontWeight="600">{s.title}</text>
      <text x={x+175} y={y+205} textAnchor="middle" fill="#566274" fontFamily="Inter, Arial, sans-serif" fontSize="12">{p.silhouette+' · '+p.eyes.length+' white '+(p.eyes.length===1?'eye':'eyes')}</text>
    </g>
  })}
</svg>)
export const mark = renderToStaticMarkup(<StackMark/>).replace('<svg ', '<svg xmlns="http://www.w3.org/2000/svg" color="#1C2B40" ')
`
const bundle = await build({
  stdin: {
    contents: entry,
    resolveDir: app,
    sourcefile: 'avatar-export.tsx',
    loader: 'tsx',
  },
  bundle: true,
  platform: 'node',
  format: 'cjs',
  jsx: 'automatic',
  write: false,
  logLevel: 'silent',
})
const generated = { exports: {} }
new Function('require', 'module', 'exports', bundle.outputFiles[0].text)(
  appRequire,
  generated,
  generated.exports
)
const { avatars, sheet, mark } = generated.exports
await mkdir(output, { recursive: true })
for (const avatar of avatars) {
  await writeFile(resolve(output, avatar.id + '.svg'), avatar.avatar + '\n')
  await writeFile(
    resolve(output, avatar.id + '-working.svg'),
    avatar.working + '\n'
  )
}
await writeFile(
  resolve(output, 'manifest.json'),
  JSON.stringify(
    avatars.map(({ avatar, working, ...metadata }) => ({
      ...metadata,
      avatar: metadata.id + '.svg',
      working: metadata.id + '-working.svg',
    })),
    null,
    2
  ) + '\n'
)
await writeFile(
  resolve(workspace, 'ui-guide/assets/agent-avatar-sheet.svg'),
  sheet + '\n'
)
await writeFile(
  resolve(workspace, 'ui-guide/assets/fourstacks-mark.svg'),
  mark + '\n'
)
console.log(
  'Exported ' +
    avatars.length +
    ' avatars, ' +
    avatars.length +
    ' working illustrations, identity manifest, family sheet and brand mark.'
)

// Include the real animation engine so the handoff components are portable.
const engineOutput = resolve(workspace, 'ui-guide/examples/agent-engine')
await mkdir(engineOutput, { recursive: true })
for (const name of [
  'BlobAgent.tsx',
  'AgentFace.tsx',
  'AgentWorkstation.tsx',
  'agentWork.ts',
  'agentDeformation.ts',
  'useAgentGaze.ts',
]) {
  await copyFile(
    resolve(app, 'src/components', name),
    resolve(engineOutput, name)
  )
}
console.log(
  'Copied the six canonical animation modules into the handoff examples.'
)
