#!/usr/bin/env bun
// Run every suite in this repository, concurrently, and report what failed.
//
// Why not `bun test`: only comp-count is a bun test file. The rest are shell
// suites and a Python bench, and bun's runner refuses any file without .test/
// .spec in the name — so `bun test` would silently run one suite out of six and
// report success. This script is the entry point; `bun run check` is the
// command. (`bun test` ignores a package.json "test" script entirely, which is
// why the script is named `check`.)

import { spawn } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')

// Suites run concurrently: each one builds its own mktemp sandbox and fakes
// launchctl/curl, so they share no port, no file and no launchd state.
const suites = [
  { name: 'repository layout', cmd: ['zsh', 'tests/test_repository_layout.sh'] },
  { name: 'setup_hermes_tools', cmd: ['sh', 'tests/test-setup-hermes-tools.sh'] },
  { name: 'comp-count', cmd: ['bun', 'test', 'desktop-plugins/comp-count/plugin.test.mjs'] },
  { name: 'provider-limits', cmd: ['bash', 'plugins/provider-limits/tests/run.sh'] },
  { name: 'launchd', cmd: ['zsh', 'runner/tests/test_launchd.sh'] },
  { name: 'webui app', cmd: ['zsh', 'runner/tests/test_webui_app.sh'] },
  // Builds and codesigns "Hermes WebUI.app" in the repository root — the one
  // suite that writes outside a temp dir, so it must not run twice at once.
  { name: 'app bundle', cmd: ['zsh', 'runner/tests/test_app_bundle.sh'] }
]

const run = suite => new Promise(resolve => {
  const started = performance.now()
  const child = spawn(suite.cmd[0], suite.cmd.slice(1), { cwd: root })
  let output = ''
  child.stdout.on('data', chunk => { output += chunk })
  child.stderr.on('data', chunk => { output += chunk })
  child.on('error', error => resolve({
    ...suite, code: 127, seconds: 0,
    output: `${output}cannot run ${suite.cmd[0]}: ${error.message}\n`
  }))
  child.on('close', code => resolve({
    ...suite, code, output, seconds: (performance.now() - started) / 1000
  }))
})

const only = process.argv.slice(2)
const selected = only.length === 0
  ? suites
  : suites.filter(suite => only.some(arg => suite.name.includes(arg)))

if (selected.length === 0) {
  console.error(`no suite matches ${only.join(' ')}`)
  console.error(`available: ${suites.map(s => s.name).join(', ')}`)
  process.exit(2)
}

const started = performance.now()
const results = await Promise.all(selected.map(run))
const total = (performance.now() - started) / 1000

for (const result of results) {
  const mark = result.code === 0 ? '✓' : '✗'
  console.log(`${mark} ${result.name.padEnd(20)} ${result.seconds.toFixed(1)}s`)
}

const failed = results.filter(result => result.code !== 0)
for (const result of failed) {
  // Full output, not a tail: a suite that fails halfway prints its reason
  // before whatever noise follows.
  console.log(`\n─── ${result.name} (exit ${result.code}) ───`)
  console.log(result.output.trimEnd())
}

console.log(`\n${results.length - failed.length}/${results.length} suites passed in ${total.toFixed(1)}s`)
process.exit(failed.length === 0 ? 0 : 1)
