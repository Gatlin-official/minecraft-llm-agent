const { Vec3 } = require('vec3')
const { goals } = require('mineflayer-pathfinder')
const { moveTo, explore } = require('./navigation')

const ACTIONS = new Set([
  'move_to', 'explore', 'collect', 'mine', 'place_block', 'craft', 'smelt',
  'attack', 'eat', 'equip', 'sleep', 'look_at', 'open_chest',
  'deposit_item', 'withdraw_item', 'wait'
])

function itemByName (bot, name) { return bot.inventory.items().find(i => i.name === name) }
function inventorySnapshot (bot) {
  return bot.inventory.items().reduce((out, item) => {
    out[item.name] = (out[item.name] || 0) + item.count
    return out
  }, {})
}
function inventoryChanges (before, after) {
  const changes = {}
  for (const name of new Set([...Object.keys(before), ...Object.keys(after)])) {
    const delta = (after[name] || 0) - (before[name] || 0)
    if (delta) changes[name] = delta
  }
  return changes
}
function actionError (reason, target, details = {}) {
  const error = new Error(reason)
  error.reason = reason
  error.target = target
  error.details = details
  return error
}
function blockByName (bot, name, distance = 64) {
  const id = bot.registry.blocksByName[name]?.id
  if (id === undefined) throw new Error(`Unknown block: ${name}`)
  return bot.findBlock({ matching: id, maxDistance: distance })
}
function positiveInt (value, fallback = 1, max = 64) {
  const n = Number(value ?? fallback)
  if (!Number.isInteger(n) || n < 1 || n > max) throw new Error(`count must be 1-${max}`)
  return n
}

async function collect (bot, args) {
  const count = positiveInt(args.count, 1, 64)

  const id = bot.registry.blocksByName[args.block]?.id

  if (id === undefined) {
    throw new Error(`Unknown block: ${args.block}`)
  }

  const before = inventorySnapshot(bot)

  let collected = 0
  let staleAttempts = 0
  const positions = []

  while (collected < count) {
    let block = null

    // ------------------------------------------------------------
    // Prefer the exact position supplied by the planner.
    // ------------------------------------------------------------

    if (
      args.position &&
      Number.isFinite(args.position.x) &&
      Number.isFinite(args.position.y) &&
      Number.isFinite(args.position.z)
    ) {
      const targetPosition = new Vec3(
        Math.floor(args.position.x),
        Math.floor(args.position.y),
        Math.floor(args.position.z)
      )

      block = bot.blockAt(targetPosition)

      if (!block || block.name !== args.block) {
        block = null
      }
    }

    // ------------------------------------------------------------
    // Otherwise search nearby.
    // ------------------------------------------------------------

    if (!block) {
      block = bot.findBlock({
        matching: id,
        maxDistance: Math.min(
          Number(args.radius) || 48,
          96
        )
      })
    }

    if (!block) {
      throw actionError(
        'target_not_found',
        args.block
      )
    }

    console.log(
      `[COLLECT] target=${block.name} ` +
      `position=${block.position.x},` +
      `${block.position.y},` +
      `${block.position.z}`
    )

    // ------------------------------------------------------------
    // Check harvesting requirements.
    // ------------------------------------------------------------

    let compatibleTool = null

    if (!block.canHarvest(null)) {
      const compatible = bot.inventory
        .items()
        .filter(item => block.canHarvest(item.type))

      if (!compatible.length) {
        const required = Object.keys(
          block.harvestTools || {}
        )
          .map(
            toolId =>
              bot.registry.items[
                Number(toolId)
              ]?.name
          )
          .filter(Boolean)

        throw actionError(
          'requires_tool',
          args.block,
          {
            required_tools: required
          }
        )
      }

      compatibleTool = compatible[0]
    }

    // ------------------------------------------------------------
    // Move to the actual block.
    // ------------------------------------------------------------

    await bot.pathfinder.goto(
      new goals.GoalNear(
        block.position.x,
        block.position.y,
        block.position.z,
        2
      )
    )

    // ------------------------------------------------------------
    // Re-read block after movement.
    // ------------------------------------------------------------

    const current = bot.blockAt(
      block.position
    )

    if (
      !current ||
      current.name !== args.block
    ) {
      staleAttempts++

      if (staleAttempts >= 5) {
        throw actionError(
          'target_not_found',
          args.block,
          { reason: 'block_changed_before_harvest' }
        )
      }

      continue
    }

    staleAttempts = 0

    if (!bot.canDigBlock(current)) {
      throw actionError(
        'block_not_diggable',
        args.block
      )
    }

    // ------------------------------------------------------------
    // Select best harvesting tool.
    // ------------------------------------------------------------

    const tool =
      bot.pathfinder.bestHarvestTool(current) ||
      compatibleTool

    if (
      tool &&
      current.canHarvest(tool.type)
    ) {
      await bot.equip(
        tool,
        'hand'
      )
    }

    // ------------------------------------------------------------
    // Final harvest check.
    // ------------------------------------------------------------

    if (
      !current.canHarvest(
        bot.heldItem?.type || null
      )
    ) {
      throw actionError(
        'requires_tool',
        args.block
      )
    }

    // ------------------------------------------------------------
    // Mine the block.
    // ------------------------------------------------------------

    await bot.dig(current)

    positions.push({
      x: current.position.x,
      y: current.position.y,
      z: current.position.z
    })

    collected++
  }

  if (!collected) {
    throw actionError(
      'target_not_found',
      args.block
    )
  }

  await new Promise(
    resolve => setTimeout(resolve, 500)
  )

  return {
    target: args.block,
    reason: 'blocks_mined',

    state_change: {
      blocks_mined: collected,

      inventory: inventoryChanges(
        before,
        inventorySnapshot(bot)
      ),

      positions: positions.slice(0, 4)
    }
  }
}

async function placeBlock (bot, args) {
  const item = itemByName(bot, args.block)
  if (!item) throw new Error(`Missing item: ${args.block}`)
  const p = args.position
  if (!p || ![p.x, p.y, p.z].every(Number.isFinite)) throw new Error('position requires x, y, z')
  const destination = new Vec3(Math.floor(p.x), Math.floor(p.y), Math.floor(p.z))
  const faces = [new Vec3(0, -1, 0), new Vec3(0, 1, 0), new Vec3(1, 0, 0), new Vec3(-1, 0, 0), new Vec3(0, 0, 1), new Vec3(0, 0, -1)]
  for (const face of faces) {
    const reference = bot.blockAt(destination.minus(face))
    if (reference && reference.boundingBox !== 'empty') {
      await bot.pathfinder.goto(new goals.GoalNear(destination.x, destination.y, destination.z, 3))
      await bot.equip(item, 'hand')
      await bot.placeBlock(reference, face)
      return { target: args.block, reason: 'block_placed', state_change: { position: { x: destination.x, y: destination.y, z: destination.z } } }
    }
  }
  throw new Error('No supporting block at position')
}

async function craft (bot, args) {
  const count = positiveInt(args.count, 1, 64)
  const item = bot.registry.itemsByName[args.item]
  if (!item) throw new Error(`Unknown item: ${args.item}`)
  const table = blockByName(bot, 'crafting_table', 32)
  const recipe = bot.recipesFor(item.id, null, count, table)[0] || bot.recipesFor(item.id, null, count, null)[0]
  if (!recipe) {
    const possible = bot.recipesAll(item.id, null, true)
    if (!table && possible.some(candidate => candidate.requiresTable)) {
      throw actionError('requires_crafting_table', args.item)
    }
    const missing = possible.map(candidate => {
      const applications = Math.ceil(count / candidate.result.count)
      return candidate.delta
      .filter(delta => delta.count < 0)
      .map(delta => ({ item: bot.registry.items[delta.id]?.name || String(delta.id), count: Math.max(0, (-delta.count * applications) - bot.inventory.count(delta.id, delta.metadata)) }))
      .filter(entry => entry.count > 0)
    }).sort((a, b) => a.length - b.length)[0] || []
    throw actionError('missing_ingredients', args.item, { missing })
  }
  if (table) await bot.pathfinder.goto(new goals.GoalNear(table.position.x, table.position.y, table.position.z, 2))
  const before = inventorySnapshot(bot)
  const applications = Math.ceil(count / recipe.result.count)
  await bot.craft(recipe, applications, recipe.requiresTable ? table : null)
  return { target: args.item, reason: 'item_crafted', state_change: { inventory: inventoryChanges(before, inventorySnapshot(bot)) } }
}

async function smelt (bot, args) {
  const input = itemByName(bot, args.item)
  const fuel = itemByName(bot, args.fuel || 'coal')
  const count = positiveInt(args.count, 1, 6)
  if (!input) throw new Error(`Missing item: ${args.item}`)
  if (!fuel) throw new Error(`Missing fuel: ${args.fuel || 'coal'}`)
  const furnaceBlock = blockByName(bot, 'furnace', 32)
  if (!furnaceBlock) throw new Error('No furnace nearby')
  await bot.pathfinder.goto(new goals.GoalNear(furnaceBlock.position.x, furnaceBlock.position.y, furnaceBlock.position.z, 2))
  const furnace = await bot.openFurnace(furnaceBlock)
  try {
    await furnace.putFuel(fuel.type, null, Math.min(fuel.count, Math.ceil(count / 8)))
    await furnace.putInput(input.type, null, Math.min(input.count, count))
    await new Promise(resolve => setTimeout(resolve, count * 10500))
    if (furnace.outputItem()) await furnace.takeOutput()
  } finally { furnace.close() }
  return { target: args.item, reason: 'item_smelted', state_change: { count } }
}

async function containerAction (bot, args, mode) {
  const chestBlock = blockByName(bot, args.container || 'chest', 32)
  if (!chestBlock) throw new Error('No container nearby')
  await bot.pathfinder.goto(new goals.GoalNear(chestBlock.position.x, chestBlock.position.y, chestBlock.position.z, 2))
  const chest = await bot.openContainer(chestBlock)
  if (mode === 'open') { chest.close(); return { target: args.container || 'chest', reason: 'container_opened', state_change: {} } }
  const count = positiveInt(args.count, 1, 64)
  try {
    if (mode === 'deposit') {
      const item = itemByName(bot, args.item)
      if (!item) throw new Error(`Missing item: ${args.item}`)
      await chest.deposit(item.type, null, Math.min(count, item.count))
    } else {
      const item = chest.containerItems().find(i => i.name === args.item)
      if (!item) throw new Error(`Container has no ${args.item}`)
      await chest.withdraw(item.type, null, Math.min(count, item.count))
    }
  } finally { chest.close() }
  return { target: args.item, reason: mode === 'deposit' ? 'item_deposited' : 'item_withdrawn', state_change: { count } }
}

async function perform (bot, name, args) {
  switch (name) {
    case 'move_to': {
      const destination = args.target || args.position
      await moveTo(bot, destination, Number(args.range) || 1)
      return { target: destination, reason: 'destination_reached', state_change: { position: destination } }
    }
    case 'explore': {
      const result = await explore(bot, args.radius, args.targets)
      return { target: args.targets || 'useful_resources', reason: result.discovered ? 'useful_target_found' : 'area_explored', state_change: result }
    }
    case 'collect': case 'mine': return collect(bot, args)
    case 'place_block': return placeBlock(bot, args)
    case 'craft': return craft(bot, args)
    case 'smelt': return smelt(bot, args)
    case 'look_at': {
      const p = args.position
      if (!p || ![p.x, p.y, p.z].every(Number.isFinite)) throw new Error('position requires x, y, z')
      await bot.lookAt(new Vec3(p.x, p.y, p.z)); return { target: p, reason: 'target_observed', state_change: {} }
    }
    case 'attack': {
      const target = Object.values(bot.entities).filter(e => e !== bot.entity && (e.name === args.entity || e.displayName === args.entity)).sort((a, b) => bot.entity.position.distanceTo(a.position) - bot.entity.position.distanceTo(b.position))[0]
      if (!target) throw new Error(`No ${args.entity} nearby`)
      await bot.pathfinder.goto(new goals.GoalFollow(target, 2))
      const deadline = Date.now() + 20000
      while (bot.entities[target.id] && Date.now() < deadline) {
        if (bot.entity.position.distanceTo(target.position) > 4) {
          await bot.pathfinder.goto(new goals.GoalFollow(target, 2))
        }
        bot.attack(target)
        await new Promise(resolve => setTimeout(resolve, 650))
      }
      return { target: args.entity, reason: bot.entities[target.id] ? 'combat_timeout' : 'target_defeated', state_change: {} }
    }
    case 'eat': {
      const item = itemByName(bot, args.item)
      if (!item) throw new Error(`Missing food: ${args.item}`)
      const beforeFood = bot.food
      await bot.equip(item, 'hand'); await bot.consume(); return { target: args.item, reason: 'food_consumed', state_change: { food_before: beforeFood, food_after: bot.food } }
    }
    case 'equip': {
      const item = itemByName(bot, args.item)
      if (!item) throw new Error(`Missing item: ${args.item}`)
      await bot.equip(item, args.destination || 'hand'); return { target: args.item, reason: 'item_equipped', state_change: { equipped: args.item } }
    }
    case 'sleep': {
      const bed = bot.findBlock({ matching: b => bot.isABed(b), maxDistance: 32 })
      if (!bed) throw new Error('No bed nearby')
      await bot.pathfinder.goto(new goals.GoalNear(bed.position.x, bed.position.y, bed.position.z, 2))
      await bot.sleep(bed); return { target: 'bed', reason: 'sleeping', state_change: {} }
    }
    case 'open_chest': return containerAction(bot, args, 'open')
    case 'deposit_item': return containerAction(bot, args, 'deposit')
    case 'withdraw_item': return containerAction(bot, args, 'withdraw')
    case 'wait': await new Promise(resolve => setTimeout(resolve, Math.min(Number(args.seconds) || 1, 60) * 1000)); return { target: null, reason: 'wait_complete', state_change: {} }
    default: throw new Error(`Unsupported action: ${name}`)
  }
}

async function executeAction (bot, request, timeoutMs) {
  const name = request?.action
  if (!ACTIONS.has(name)) return { success: false, action: name, error: 'Invalid action' }
  const timer = new Promise((_, reject) => setTimeout(() => reject(new Error('Action timeout')), timeoutMs))
  try {
    const result = await Promise.race([perform(bot, name, request), timer])
    return { success: true, action: name, ...result }
  } catch (error) {
    bot.pathfinder?.setGoal(null)
    return {
      success: false,
      action: name,
      target: error.target || request.block || request.item || request.entity || null,
      reason: error.reason || error.message,
      details: error.details || {},
      state_change: {}
    }
  }
}

module.exports = { ACTIONS, executeAction }
