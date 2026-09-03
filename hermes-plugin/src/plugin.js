import { host } from '@hermes/plugin-sdk'

import { createConnector } from './connector-core.js'

const plugin = {
  id: 'openwebui-bridge',
  name: 'OpenWebUI Bridge',
  description: 'Continues Hermes Desktop sessions from the local OpenWebUI bridge.',
  register(ctx) {
    for (const capability of ['profileRoutes', 'retainProfile', 'requestProfile', 'onEvent']) {
      if (typeof host[capability] !== 'function') {
        throw new Error(`OpenWebUI Bridge requires host.${capability}; update Hermes Desktop`)
      }
    }

    const connector = createConnector({
      host,
      WebSocketImpl: WebSocket,
      cryptoImpl: crypto,
      url: `ws://127.0.0.1:${__HERMES_BRIDGE_HOST_PORT__}/connector`,
      secret: __HERMES_BRIDGE_SECRET__
    })
    connector.start()
    ctx.onDispose(() => connector.stop())
  }
}

export default plugin
