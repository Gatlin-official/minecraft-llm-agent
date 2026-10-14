const mineflayer = require('mineflayer')
const { pathfinder, Movements } = require('mineflayer-pathfinder')
const config = require('./config')

let bot = null
let stopping = false

function connect () {
  const options = { ...config.minecraft, logErrors: false }
  if (!options.version) delete options.version
  bot = mineflayer.createBot(options)
  bot.loadPlugin(pathfinder)
  console.log(`[MC] Connecting to ${options.host}:${options.port}`)
  bot.once('login', () => console.log(`[MC] Connected as ${bot.username}`))
  bot.once('spawn', () => {
    const movements = new Movements(bot)
    bot.pathfinder.setMovements(movements)
    const p = bot.entity.position
    console.log(`[MC] Spawned at ${p.x.toFixed(1)} ${p.y.toFixed(1)} ${p.z.toFixed(1)}`)
  })
  bot.on('kicked', reason => console.error('[MC] Kicked:', String(reason)))
  bot.on('error', error => {
    const details = error.message || error.errors?.map(item => item.message).join('; ') || String(error)
    console.error('[MC] Error:', details)
  })
  bot.on('end', reason => {
    console.log(`[MC] Disconnected: ${reason}`)
    if (!stopping) {
      console.log('[MC] Reconnecting in 5 seconds')
      setTimeout(connect, 5000)
    }
  })
  return bot
}

function getBot () { return bot }
function isReady () { return Boolean(bot && bot.entity && bot.player) }
function stop () {
  stopping = true
  if (bot) {
    bot.pathfinder?.setGoal(null)
    bot.quit('Agent stopped')
  }
}
function isStopping () { return stopping }

module.exports = { connect, getBot, isReady, stop, isStopping }
