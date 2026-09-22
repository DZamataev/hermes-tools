import { expect, test } from 'bun:test'
import { mkdtemp, mkdir, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

// plugin.js imports '@hermes/plugin-sdk' and 'react/jsx-runtime', which only
// exist inside the desktop app. Stand up a throwaway node_modules with the
// handful of exports it uses and let real module resolution do the rest —
// rewriting the import specifiers to data: URLs, as this test once did, is not
// supported by every runtime and hid the plugin's real resolution path.
async function loadPlugin(usage) {
  const dir = await mkdtemp(join(tmpdir(), 'comp-count-'))
  const sdk = join(dir, 'node_modules', '@hermes', 'plugin-sdk')
  const react = join(dir, 'node_modules', 'react', 'jsx-runtime')
  await mkdir(sdk, { recursive: true })
  await mkdir(react, { recursive: true })

  await writeFile(join(dir, 'package.json'), JSON.stringify({ name: 'probe', type: 'module' }))
  await writeFile(join(sdk, 'package.json'), JSON.stringify(
    { name: '@hermes/plugin-sdk', type: 'module', main: 'index.js' }))
  await writeFile(join(sdk, 'index.js'), `
    export const host = { state: { focusedUsage: ${JSON.stringify(usage)} } }
    export const STATUSBAR_AREAS = { right: 'statusBar.right' }
    export const useValue = value => value
  `)
  await writeFile(join(dir, 'node_modules', 'react', 'package.json'), JSON.stringify({
    name: 'react', type: 'module', exports: { './jsx-runtime': './jsx-runtime/index.js' }
  }))
  await writeFile(join(react, 'index.js'),
    'export const jsx = (type, props) => ({ type, props })\n')

  await writeFile(join(dir, 'plugin.js'), await Bun.file(
    new URL('./plugin.js', import.meta.url)).text())
  return (await import(join(dir, 'plugin.js'))).default
}

function renderChip(plugin) {
  let contribution
  plugin.register({ register(value) { contribution = value } })
  const element = contribution.render()
  return { contribution, chip: element.type(element.props) }
}

test('shows the focused session compaction count in the right status bar', async () => {
  const plugin = await loadPlugin({ compressions: 3 })
  const { contribution, chip } = renderChip(plugin)

  expect(plugin.id).toBe('comp-count')
  expect(contribution.area).toBe('statusBar.right')
  expect(chip.props.children).toBe('🧳 3')
  expect(chip.props.title).toBe('Compactions in this session: 3')
})

// The plugin clamps with Math.max(0, Math.trunc(Number(x) || 0)): the host can
// report a count before the session state settles, and "🧳 NaN" or a negative
// count in the status bar is worse than showing zero.
test.each([
  [{ compressions: 0 }, '🧳 0'],
  [{ compressions: 2.9 }, '🧳 2'],
  [{ compressions: -4 }, '🧳 0'],
  [{ compressions: 'nonsense' }, '🧳 0'],
  [{ compressions: null }, '🧳 0'],
  [{}, '🧳 0'],
  [null, '🧳 0']
])('renders %o as %s', async (usage, expected) => {
  const { chip } = renderChip(await loadPlugin(usage))
  expect(chip.props.children).toBe(expected)
})
