const fs = require('fs')
const path = require('path')

const root = path.resolve(__dirname, '../..')
const configPath = process.env.CONFIG_PATH || path.join(root, 'config', 'config.json')
const fallbackPath = path.join(root, 'config', 'config.example.json')
const file = fs.existsSync(configPath) ? configPath : fallbackPath
const raw = JSON.parse(fs.readFileSync(file, 'utf8'))

module.exports = {
  minecraft: {
    host: process.env.MC_HOST || raw.minecraft.host,
    port: Number(process.env.MC_PORT || raw.minecraft.port),
    username: process.env.MC_USERNAME || raw.minecraft.username,
    auth: process.env.MC_AUTH || raw.minecraft.auth || 'offline',
    version: process.env.MC_VERSION || raw.minecraft.version || false
  },
  serverPort: Number(process.env.BOT_PORT || raw.bot_server_port),
  actionTimeoutMs: Number(raw.action_timeout_seconds || 30) * 1000
}
