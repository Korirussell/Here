// Writes extras/dashboard/architecture.svg: the static "How HERE works" diagram for the submission, drawn by the
// same function as the dashboard's live "How it works" view.
//   node extras/dashboard/make_architecture.mjs
// PNG: open the SVG in Chrome headless (see extras/dashboard/README.md, "How it works").
import { writeFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { archSvg } from './static/architecture.js'

const out = fileURLToPath(new URL('./architecture.svg', import.meta.url))
writeFileSync(out, archSvg() + '\n')
console.log(`wrote ${out}`)
