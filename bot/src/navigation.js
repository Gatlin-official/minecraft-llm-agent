const { goals } = require('mineflayer-pathfinder')

async function moveTo (bot, target, range = 1) {
  const { x, y, z } = target || {}
  if (![x, y, z].every(Number.isFinite)) throw new Error('target requires numeric x, y, z')
  await bot.pathfinder.goto(new goals.GoalNear(x, y, z, range))
}

async function explore (bot, radius = 32, targets = []) {
  const distance = Math.max(8, Math.min(Number(radius) || 32, 96))
  const requested = Array.isArray(targets) ? targets : []
  const defaults = ['oak_log', 'birch_log', 'spruce_log', 'jungle_log', 'acacia_log', 'dark_oak_log', 'mangrove_log', 'cherry_log', 'coal_ore', 'iron_ore', 'chest', 'hay_block']
  const names = [...new Set([...requested, ...defaults])]
  const ids = names.map(name => bot.registry.blocksByName[name]?.id).filter(id => id !== undefined)
  const angle = Math.random() * Math.PI * 2
  const origin = bot.entity.position.clone()

  for (let walked = 12; walked <= distance; walked += 12) {
    await bot.pathfinder.goto(new goals.GoalNear(
      Math.floor(origin.x + Math.cos(angle) * walked), Math.floor(bot.entity.position.y),
      Math.floor(origin.z + Math.sin(angle) * walked), 3
    ))
    const found = ids.length ? bot.findBlock({ matching: ids, maxDistance: 16 }) : null
    if (found) {
      return {
        travelled: Math.round(bot.entity.position.distanceTo(origin)),
        discovered: { type: found.name, position: { x: found.position.x, y: found.position.y, z: found.position.z } }
      }
    }
  }
  return { travelled: Math.round(bot.entity.position.distanceTo(origin)), discovered: null }
}

module.exports = { moveTo, explore }
