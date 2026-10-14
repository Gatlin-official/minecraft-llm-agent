const USEFUL_BLOCKS = new Set([
  'oak_log', 'birch_log', 'spruce_log', 'jungle_log', 'acacia_log', 'dark_oak_log',
  'mangrove_log', 'cherry_log',
  'stone', 'coal_ore', 'iron_ore', 'copper_ore', 'gold_ore', 'diamond_ore',
  'crafting_table', 'furnace', 'chest', 'white_bed', 'hay_block', 'wheat'
])

const HOSTILES = new Set([
  'zombie', 'skeleton', 'creeper', 'spider', 'cave_spider', 'witch', 'drowned',
  'husk', 'stray', 'pillager', 'vindicator', 'enderman', 'slime', 'phantom'
])

function inventorySummary (bot) {
  return bot.inventory.items().reduce((out, item) => {
    out[item.name] = (out[item.name] || 0) + item.count
    return out
  }, {})
}

function nearbyEntities (bot, radius = 24) {
  return Object.values(bot.entities)
    .filter(e => e !== bot.entity && e.position && e.position.distanceTo(bot.entity.position) <= radius)
    .map(e => {
      const type = e.name || e.displayName || e.type
      return {
        type,
        distance: Math.round(e.position.distanceTo(bot.entity.position)),
        hostile: HOSTILES.has(type),
        position: { x: Math.floor(e.position.x), y: Math.floor(e.position.y), z: Math.floor(e.position.z) }
      }
    })
    .sort((a, b) => a.distance - b.distance).slice(0, 16)
}

function nearbyBlocks (bot, radius = 16) {
  const found = []
  for (const name of USEFUL_BLOCKS) {
    const id = bot.registry.blocksByName[name]?.id
    if (id === undefined) continue
    const positions = bot.findBlocks({ matching: id, maxDistance: radius, count: 3 })
    for (const p of positions) {
      found.push({
        type: name,
        distance: Math.round(p.distanceTo(bot.entity.position)),
        position: { x: p.x, y: p.y, z: p.z }
      })
    }
  }
  return found.sort((a, b) => a.distance - b.distance).slice(0, 20)
}

function getState (bot) {
  if (!bot?.entity) return { connected: false }
  const p = bot.entity.position
  return {
    connected: true,
    username: bot.username,
    position: { x: Math.floor(p.x), y: Math.floor(p.y), z: Math.floor(p.z) },
    health: bot.health,
    food: bot.food,
    dimension: bot.game?.dimension,
    time: bot.time?.timeOfDay,
    inventory: inventorySummary(bot),
    equipped: bot.heldItem?.name || null,
    tools: bot.inventory.items().filter(item => /_(pickaxe|axe|shovel|sword|hoe)$/.test(item.name)).map(item => item.name),
    nearby_entities: nearbyEntities(bot),
    nearby_blocks: nearbyBlocks(bot)
  }
}

module.exports = { getState, inventorySummary }
