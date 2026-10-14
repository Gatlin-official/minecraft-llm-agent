class HierarchicalPlanner:
    """Builds a stable high-level -> subgoal -> action hierarchy."""

    def build(self, goals, assessment=None, world=None, learned=None):
        objective = goals.get("goal_objective") or {}
        current = goals.get("current_goal")
        if not current:
            return {
                "level": "high",
                "high_level_goal": None,
                "subgoal": None,
                "action_intent": "choose a useful safe goal",
            }

        subgoal = goals.get("current_subgoal")
        requirement = goals.get("next_requirement")
        if requirement:
            action_intent = self._intent(requirement)
        else:
            action_intent = "advance the current goal and verify progress"

        return {
            "level": "action",
            "high_level_goal": current,
            "purpose": goals.get("goal_purpose"),
            "objective": objective,
            "subgoal": subgoal,
            "requirement": requirement,
            "action_intent": action_intent,
            "progress_percent": (assessment or {}).get("progress_percent", goals.get("goal_progress", {}).get("percent", 0) if isinstance(goals.get("goal_progress"), dict) else 0),
            "blockers": goals.get("blockers", []),
            "world": world or {},
            "learned": learned or {},
            "policy": [
                "Never promote a prerequisite into the high-level goal.",
                "Choose the next action that advances the current subgoal.",
                "Verify state change after meaningful actions.",
                "Use learned experience as evidence, not as an override of current state.",
            ],
        }

    @staticmethod
    def _intent(requirement):
        source = requirement.get("source")
        item = requirement.get("item")
        missing = requirement.get("missing", 0)
        if source == "craft":
            return f"craft {item} x{missing}"
        blocks = requirement.get("gather_blocks") or []
        if blocks:
            return f"obtain {item} by gathering {blocks[0]} x{missing}"
        return f"obtain {item} x{missing}"
