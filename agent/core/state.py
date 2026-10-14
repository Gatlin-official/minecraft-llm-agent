FOOD_ITEMS = {
    "bread", "cooked_beef", "cooked_porkchop", "cooked_chicken", "cooked_mutton",
    "cooked_rabbit", "baked_potato", "apple", "carrot", "potato", "beef",
    "porkchop", "chicken", "mutton", "rabbit", "melon_slice", "sweet_berries",
}


def _count_matching(inventory, predicate):
    return sum(count for name, count in inventory.items() if predicate(name))


def analyze_state(state):
    inventory = state.get("inventory", {})
    nearby = {block.get("type") for block in state.get("nearby_blocks", [])}
    tools = set(state.get("tools", []))
    hostiles = [entity for entity in state.get("nearby_entities", []) if entity.get("hostile") and entity.get("distance", 99) <= 10]
    food = {name: count for name, count in inventory.items() if name in FOOD_ITEMS and count > 0}
    health = state.get("health") if isinstance(state.get("health"), (int, float)) else 20
    hunger = state.get("food") if isinstance(state.get("food"), (int, float)) else 20
    logs = _count_matching(inventory, lambda name: name.endswith("_log") or name.endswith("_stem"))
    planks = _count_matching(inventory, lambda name: name.endswith("_planks"))
    pickaxes = [name for name in tools if name.endswith("_pickaxe")]
    advice = []
    if hostiles and health <= 12:
        advice.append("danger_close: retreat or defend before gathering")
    if hunger <= 12:
        advice.append(f"hunger_low: {'eat available food' if food else 'obtain food'}")
    if not pickaxes and ("stone" in nearby or any(name.endswith("_ore") for name in nearby)):
        advice.append("missing_pickaxe: obtain wood, planks, sticks, a crafting table, then a pickaxe")
    if logs == 0 and planks == 0:
        advice.append("no_wood: gather a visible log or explore specifically for logs")
    elif planks == 0:
        advice.append("logs_available: craft matching planks")
    elif inventory.get("stick", 0) < 2:
        advice.append("need_sticks: craft sticks before tools")
    if inventory.get("crafting_table", 0) and "crafting_table" not in nearby:
        advice.append("crafting_table_in_inventory: place it before crafting tools")
    if pickaxes and inventory.get("cobblestone", 0) >= 3 and not any(name.startswith("stone_") for name in tools):
        advice.append("stone_upgrade_available: craft stone equipment when useful")
    return {
        "nearby_block_types": sorted(nearby),
        "nearby_danger": [entity["type"] for entity in hostiles],
        "food_items": food,
        "wood": {"logs": logs, "planks": planks, "sticks": inventory.get("stick", 0)},
        "pickaxes": pickaxes,
        "advice": advice[:4],
    }


def compact_state(state):
    """Keep only decision-relevant bounded state."""
    return {
        "position": state.get("position"),
        "health": state.get("health"),
        "food": state.get("food"),
        "dimension": state.get("dimension"),
        "time": state.get("time"),
        "inventory": state.get("inventory", {}),
        "equipped": state.get("equipped"),
        "tools": state.get("tools", []),
        "nearby_entities": state.get("nearby_entities", [])[:12],
        "nearby_blocks": state.get("nearby_blocks", [])[:15],
        "understanding": analyze_state(state),
    }
