from collections import deque


class ShortTermMemory:
    def __init__(self, limit=20):
        self.events = deque(maxlen=limit)

    def add(self, action, result):
        compact_action = {
            key: value for key, value in action.items()
            if key in {"action", "block", "item", "entity", "count", "radius", "targets", "target"}
        }
        compact_result = {
            key: value for key, value in result.items()
            if key in {"success", "action", "target", "reason", "details", "state_change"}
        }
        self.events.append({"action": compact_action, "result": compact_result})

    def summary(self):
        return list(self.events)
