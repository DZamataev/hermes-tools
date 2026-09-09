import { host, STATUSBAR_AREAS, useValue } from '@hermes/plugin-sdk'
import { jsx } from 'react/jsx-runtime'

function CompCount() {
  const usage = useValue(host.state.focusedUsage)
  const count = Math.max(0, Math.trunc(Number(usage?.compressions) || 0))

  return jsx('span', {
    className: 'px-1.5 text-[0.6875rem] tabular-nums text-(--ui-text-tertiary)',
    title: `Compactions in this session: ${count}`,
    'aria-label': `Compactions in this session: ${count}`,
    children: `🧳 ${count}`
  })
}

export default {
  id: 'comp-count',
  name: 'Comp Count',
  description: 'Shows the focused session compaction count in the status bar.',
  register(ctx) {
    ctx.register({
      id: 'status',
      area: STATUSBAR_AREAS.right,
      order: 120,
      render: () => jsx(CompCount, {})
    })
  }
}
