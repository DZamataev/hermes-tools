import assert from 'node:assert/strict'
import { readFile, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'

const pluginPath = new URL('./plugin.js', import.meta.url)

test('shows the focused session compaction count in the right status bar', async () => {
  const source = await readFile(pluginPath, 'utf8')
  const sdkStub = `
    export const host = { state: { focusedUsage: { get: () => ({ compressions: 3 }) } } };
    export const STATUSBAR_AREAS = { right: 'statusBar.right' };
    export const useValue = atom => atom.get();
  `
  const jsxStub = `export const jsx = (type, props) => ({ type, props });`
  const instrumented = source
    .replace("'@hermes/plugin-sdk'", JSON.stringify(`data:text/javascript,${encodeURIComponent(sdkStub)}`))
    .replace("'react/jsx-runtime'", JSON.stringify(`data:text/javascript,${encodeURIComponent(jsxStub)}`))
  const modulePath = join(tmpdir(), `comp-count-${process.pid}-${Date.now()}.mjs`)
  await writeFile(modulePath, instrumented)
  const plugin = (await import(`${new URL(`file://${modulePath}`)}?v=${Date.now()}`)).default

  let contribution
  plugin.register({ register(value) { contribution = value } })

  assert.equal(plugin.id, 'comp-count')
  assert.equal(contribution.area, 'statusBar.right')
  const component = contribution.render()
  const button = component.type(component.props)
  assert.equal(button.props.children, '🧳 3')
  assert.equal(button.props.title, 'Compactions in this session: 3')
})
