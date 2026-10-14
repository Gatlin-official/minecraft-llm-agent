import math


class WorldModel:
    """Small persistent-in-process world model used to guide planning."""

    def __init__(self):
        self.observations = []
        self.visited_regions = set()
        self.last_state = None

    @staticmethod
    def _region(position):
        if not isinstance(position, dict):
            return None
        try:
            return (int(float(position.get("x", 0))) // 16, int(float(position.get("z", 0))) // 16)
        except (TypeError, ValueError):
            return None

    def observe(self, state):
        state = state if isinstance(state, dict) else {}
        region = self._region(state.get("position"))
        if region is not None:
            self.visited_regions.add(region)
        self.observations.append({
            "position": state.get("position"),
            "region": region,
            "nearby_blocks": [b.get("type") for b in state.get("nearby_blocks", []) if isinstance(b, dict)],
            "hostiles": [e.get("type") for e in state.get("nearby_entities", []) if isinstance(e, dict) and e.get("hostile")],
        })
        self.observations = self.observations[-20:]
        self.last_state = state
        return self.context(state)

    def context(self, state=None):
        state = state or self.last_state or {}
        health = state.get("health", 20)
        food = state.get("food", 20)
        hostiles = [e for e in state.get("nearby_entities", []) if isinstance(e, dict) and e.get("hostile") and e.get("distance", 99) <= 10]
        return {
            "risk": {
                "health": health,
                "food": food,
                "nearby_hostiles": len(hostiles),
                "danger": bool(hostiles and health <= 12),
            },
            "exploration": {
                "current_region": self._region(state.get("position")),
                "visited_region_count": len(self.visited_regions),
                "novel_region": self._region(state.get("position")) not in {x.get("region") for x in self.observations[:-1]},
            },
            "known_nearby_blocks": sorted({b.get("type") for b in state.get("nearby_blocks", []) if isinstance(b, dict) and b.get("type")}),
        }
