import { mkdir, readFile } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'

import { build } from 'esbuild'

const secret = process.env.HERMES_BRIDGE_SECRET || ''
const portText = process.env.BRIDGE_HOST_PORT || '8787'
const port = Number(portText)
const outfile = resolve(process.env.HERMES_PLUGIN_OUTFILE || 'dist/plugin.js')

if (secret.length < 32) {
  throw new Error('HERMES_BRIDGE_SECRET must contain at least 32 characters')
}
if (!Number.isInteger(port) || port < 1 || port > 65_535 || String(port) !== portText) {
  throw new Error('BRIDGE_HOST_PORT must be an integer between 1 and 65535')
}

await mkdir(dirname(outfile), { recursive: true })
await build({
  bundle: true,
  define: {
    __HERMES_BRIDGE_HOST_PORT__: JSON.stringify(portText),
    __HERMES_BRIDGE_SECRET__: JSON.stringify(secret)
  },
  entryPoints: ['src/plugin.js'],
  external: ['@hermes/plugin-sdk'],
  format: 'esm',
  logLevel: 'warning',
  outfile,
  platform: 'browser',
  target: ['es2022']
})

const output = await readFile(outfile, 'utf8')
if (output.includes('__HERMES_BRIDGE_SECRET__') || output.includes('__HERMES_BRIDGE_HOST_PORT__')) {
  throw new Error('plugin bundle still contains an uninjected configuration placeholder')
}
