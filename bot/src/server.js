const http = require('http')
const config = require('./config')
const minecraft = require('./minecraft')
const { getState } = require('./state')
const { executeAction } = require('./actions')
const { buildRequirements } = require('./requirements')

let busy = false
minecraft.connect()

function send (res, status, body) {
  res.writeHead(status, { 'Content-Type': 'application/json' })
  res.end(JSON.stringify(body))
}

function readJson (req) {
  return new Promise((resolve, reject) => {
    let body = ''
    req.on('data', chunk => {
      body += chunk
      if (body.length > 64 * 1024) reject(new Error('Request too large'))
    })
    req.on('end', () => {
      try { resolve(body ? JSON.parse(body) : {}) } catch (_) { reject(new Error('Invalid JSON')) }
    })
    req.on('error', reject)
  })
}

const server = http.createServer(async (req, res) => {
  try {
    if (req.method === 'GET' && req.url === '/health') return send(res, 200, { ok: true, minecraft: minecraft.isReady(), busy })
    if (req.method === 'GET' && req.url === '/state') return send(res, 200, getState(minecraft.getBot()))
    if (req.method === 'POST' && req.url === '/requirements') {
      if (!minecraft.isReady()) return send(res, 503, { success: false, error: 'Minecraft bot is not ready' })
      const request = await readJson(req)
      return send(res, 200, buildRequirements(minecraft.getBot(), request.item, Number(request.count) || 1))
    }
    if (req.method === 'POST' && req.url === '/stop') {
      minecraft.stop(); return send(res, 200, { success: true, message: 'Bot stopped' })
    }
    if (req.method === 'POST' && req.url === '/action') {
      if (!minecraft.isReady()) return send(res, 503, { success: false, error: 'Minecraft bot is not ready' })
      if (busy) return send(res, 409, { success: false, error: 'Another action is running' })
      const action = await readJson(req)
      busy = true
      try {
        const target = action.block || action.item || action.entity || action.target || action.targets || null
        console.log(`[NODE ACTION] ${action.action} target=${JSON.stringify(target)}`)
        const result = await executeAction(minecraft.getBot(), action, config.actionTimeoutMs)
        console.log(`[RESULT] ${result.success ? 'SUCCESS' : 'FAILED'} reason=${result.reason}`)
        if (result.state_change && Object.keys(result.state_change).length) console.log(`[RESULT] change=${JSON.stringify(result.state_change)}`)
        return send(res, result.success ? 200 : 400, result)
      } finally { busy = false }
    }
    send(res, 404, { error: 'Not found' })
  } catch (error) { send(res, 400, { success: false, error: error.message }) }
})

server.listen(config.serverPort, '127.0.0.1', () => console.log(`[API] http://127.0.0.1:${config.serverPort}`))

function shutdown () {
  minecraft.stop()
  server.close(() => process.exit(0))
  setTimeout(() => process.exit(1), 3000).unref()
}
process.on('SIGINT', shutdown)
process.on('SIGTERM', shutdown)
