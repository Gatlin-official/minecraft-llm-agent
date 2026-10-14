function inventoryCounts (bot) {
  return bot.inventory.items().reduce((out, item) => {
    out[item.name] = (out[item.name] || 0) + item.count
    return out
  }, {})
}

function ingredientEntries (bot, recipe, applications) {
  return recipe.delta
    .filter(entry => entry.count < 0)
    .map(entry => ({
      item: bot.registry.items[entry.id]?.name || String(entry.id),
      count: -entry.count * applications
    }))
}

function recipeScore (bot, recipe, available) {
  const applications = 1
  return ingredientEntries(bot, recipe, applications).reduce((score, ingredient) => {
    const inventoryScore = Math.min(available[ingredient.item] || 0, ingredient.count) * 10
    const sourceIds = gatherBlocks(bot, ingredient.item)
      .map(name => bot.registry.blocksByName[name]?.id)
      .filter(id => id !== undefined)
    const visibleScore = sourceIds.length && bot.findBlock({ matching: sourceIds, maxDistance: 32 }) ? 3 : 0
    return score + inventoryScore + visibleScore
  }, recipe.requiresTable ? -1 : 0)
}

function harvestTools (bot, itemName) {
  const block = bot.registry.blocksByName[itemName]
  if (!block?.harvestTools) return []
  return Object.keys(block.harvestTools)
    .map(id => bot.registry.items[Number(id)]?.name)
    .filter(Boolean)
}

function gatherBlocks (bot, itemName) {
  const item = bot.registry.itemsByName[itemName]
  if (!item) return []
  return (bot.registry.blocksArray || [])
    .filter(block => Array.isArray(block.drops) && block.drops.includes(item.id))
    .map(block => block.name)
    .slice(0, 16)
}

function buildRequirements (bot, itemName, count = 1, maxDepth = 7) {
  const item = bot.registry.itemsByName[itemName]
  if (!item) throw new Error(`Unknown item: ${itemName}`)
  const available = inventoryCounts(bot)

  function resolve (name, required, depth, ancestry) {
    const current = Math.min(available[name] || 0, required)
    available[name] = Math.max(0, (available[name] || 0) - current)
    const missing = required - current
    const node = { item: name, required, current, missing, source: 'inventory', requires_table: false, required_tools: [], gather_blocks: [], prerequisites: [], ingredients: [] }
    if (!missing) return node
    if (depth >= maxDepth || ancestry.has(name)) {
      node.source = 'gather'
      node.gather_blocks = gatherBlocks(bot, name)
      node.required_tools = harvestTools(bot, node.gather_blocks[0] || name)
      addToolPrerequisite(node, depth, ancestry)
      return node
    }

    const target = bot.registry.itemsByName[name]
    if (!target) return node
    const recipes = bot.recipesAll(target.id, null, true)
      .slice()
      .sort((a, b) => recipeScore(bot, b, available) - recipeScore(bot, a, available))
    const recipe = recipes[0]
    if (!recipe) {
      node.source = 'gather'
      node.gather_blocks = gatherBlocks(bot, name)
      node.required_tools = harvestTools(bot, node.gather_blocks[0] || name)
      addToolPrerequisite(node, depth, ancestry)
      return node
    }

    const applications = Math.ceil(missing / recipe.result.count)
    node.source = 'craft'
    node.requires_table = Boolean(recipe.requiresTable)
    const nextAncestry = new Set(ancestry)
    nextAncestry.add(name)
    node.ingredients = ingredientEntries(bot, recipe, applications)
      .map(ingredient => resolve(ingredient.item, ingredient.count, depth + 1, nextAncestry))
    return node
  }

  function addToolPrerequisite (node, depth, ancestry) {
    if (!node.required_tools.length || depth >= maxDepth) return
    if (node.required_tools.some(name => (available[name] || 0) > 0)) return

    const candidates = node.required_tools
      .map(name => {
        const target = bot.registry.itemsByName[name]
        if (!target) return null
        const recipes = bot.recipesAll(target.id, null, true)
        if (!recipes.length) return null
        return {
          name,
          score: Math.max(...recipes.map(recipe => recipeScore(bot, recipe, available)))
        }
      })
      .filter(Boolean)
      .sort((a, b) => b.score - a.score)

    if (candidates.length) {
      node.prerequisites.push(resolve(candidates[0].name, 1, depth + 1, new Set(ancestry)))
    }
  }

  const tree = resolve(itemName, count, 0, new Set())
  return { schema: 'requirement_plan.v1', item: itemName, count, inventory: inventoryCounts(bot), tree }
}

module.exports = { buildRequirements }
