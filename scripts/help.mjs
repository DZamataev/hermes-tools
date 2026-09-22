#!/usr/bin/env bun
// `bun run help` — what every script in this repository does.
//
// The command list is READ FROM package.json rather than retyped here, and any
// script without an entry below is printed as undocumented. Help that drifts
// from the thing it documents is worse than no help: it is believed.

import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const { scripts } = await Bun.file(join(root, 'package.json')).json()

const docs = {
  help: {
    summary: 'Print this list.',
    examples: ['bun run help']
  },
  check: {
    summary: 'Run every test suite in this repository, concurrently (~9s).',
    detail: [
      'Prints one line per suite and the FULL output of whichever failed.',
      'An argument keeps only the suites whose name contains it.',
      'NOT `bun test`: bun\'s own runner collects only files with .test or',
      '.spec in the name, so it would run the single comp-count file, skip the',
      'five shell suites and the Python bench, and report success.'
    ],
    examples: [
      'bun run check',
      'bun run check comp-count       # one suite, ~0.2s',
      'bun run check provider webui   # several by substring'
    ]
  },
  bootstrap: {
    summary: 'Fetch the hermes-webui submodule. Run once after cloning.',
    examples: ['bun run bootstrap']
  },
  app: {
    summary: 'Build "Hermes WebUI.app" in the repository root.',
    detail: [
      'The applet records the path of the checkout that built it, so rebuild',
      'after moving the repository; the bundle itself can move to',
      '/Applications freely. Set HERMES_WEBUI_ICON if Hermes is not at ~/.hermes.'
    ],
    examples: [
      'bun run app',
      'HERMES_WEBUI_ICON=~/icons/hermes.icns bun run app'
    ]
  },
  'webui:enable': {
    summary: 'Start Hermes WebUI now and at every login (LaunchAgent).',
    detail: [
      'Binds 0.0.0.0:8787, so configure WebUI authentication before enabling.',
      'Mutually exclusive with the Dock app on the same port.'
    ],
    examples: ['bun run webui:enable']
  },
  'webui:status': {
    summary: 'Report whether the LaunchAgent is loaded and responding.',
    examples: ['bun run webui:status']
  },
  'webui:restart': {
    summary: 'Restart the LaunchAgent-managed WebUI.',
    examples: ['bun run webui:restart']
  },
  'webui:disable': {
    summary: 'Stop the WebUI and remove it from launch-at-login.',
    examples: ['bun run webui:disable']
  }
}

// Grouped for reading order; anything ungrouped still gets printed below.
const groups = [
  ['Everyday', ['check', 'help']],
  ['Setup', ['bootstrap', 'app']],
  ['Hermes WebUI service', ['webui:enable', 'webui:status', 'webui:restart', 'webui:disable']]
]

const known = new Set(groups.flatMap(([, names]) => names))
const ungrouped = Object.keys(scripts).filter(name => !known.has(name))
if (ungrouped.length > 0) groups.push(['Other', ungrouped])

const pad = '  '
console.log('\nHermes Tools — local macOS tooling for Hermes WebUI and Desktop plugins.\n')

for (const [title, names] of groups) {
  const present = names.filter(name => name in scripts || name in docs)
  if (present.length === 0) continue
  console.log(`${title}`)
  for (const name of present) {
    const entry = docs[name]
    console.log(`${pad}bun run ${name}`)
    if (!entry) {
      console.log(`${pad}${pad}(undocumented — add it to scripts/help.mjs)`)
      continue
    }
    console.log(`${pad}${pad}${entry.summary}`)
    for (const line of entry.detail ?? []) console.log(`${pad}${pad}${line}`)
    if (entry.examples?.length > 1 || entry.detail) {
      for (const example of entry.examples ?? []) console.log(`${pad}${pad}$ ${example}`)
    }
    console.log('')
  }
}

console.log(`Not a bun script: ./setup_hermes_tools.sh installs both plugins into
~/.hermes and enables the provider-limits backend. It is a plain shell script
because it runs on machines that have no bun.

The provider-limits bench takes a flag bun scripts do not pass through:
${pad}$ plugins/provider-limits/tests/run.sh --e2e   # live upstreams, needs keys

Full documentation: README.md`)
