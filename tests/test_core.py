import unittest
import json

from agent.core.goals import GoalManager
from agent.core.planner import Planner


class CoreBehaviorTests(unittest.TestCase):
    def setUp(self):
        self.planner = object.__new__(Planner)
        self.planner.max_batch = 3

    def test_goal_persists_without_replace_status(self):
        goals = GoalManager()
        goals.update("Get basic tools")
        goals.update("Gather dirt", "continue")
        self.assertEqual(goals.current, "Get basic tools")

    def test_structured_memory_is_safely_normalized(self):
        decision = self.planner.validate(
            {"goal": "Explore", "actions": [{"action": "wait"}], "memory": {"fact": "Village east"}},
            {"inventory": {}, "nearby_blocks": []},
            {"current_goal": "Explore"},
        )
        self.assertEqual(decision["memory"], "Village east")

    def test_stone_requires_pickaxe(self):
        with self.assertRaisesRegex(ValueError, "requires_pickaxe"):
            self.planner.validate(
                {"goal": "Get stone", "actions": [{"action": "mine", "block": "stone"}]},
                {"inventory": {}, "nearby_blocks": [{"type": "stone"}]},
                {"current_goal": "Get stone"},
            )

    def test_planner_sends_explicit_goal_context(self):
        class CapturingLLM:
            user = None

            def complete_json(self, _system, user, response_schema=None):
                self.user = user
                self.response_schema = response_schema
                return {
                    "goal": "Gather wood",
                    "goal_status": "continue",
                    "progress": "Need logs",
                    "reason": "No wood in inventory",
                    "actions": [{"action": "explore", "targets": ["oak_log"]}],
                    "memory": None,
                }

        llm = CapturingLLM()
        planner = Planner(llm, "test", 3)
        planner.decide(
            {"inventory": {}, "nearby_blocks": []},
            {"mode": "free_play", "current_goal": None, "goal_status": "choose", "goal_progress": None},
            [],
            [],
        )
        context = json.loads(llm.user.split("PLANNER_CONTEXT_JSON:\n", 1)[1])
        self.assertNotIn("goal", context)
        self.assertIsNone(context["current_goal"])
        self.assertEqual(context["goal_status"], "choose")
        self.assertIn("goal_progress", context)

    def test_rejected_decision_records_compact_keys(self):
        class MissingGoalLLM:
            def complete_json(self, _system, _user, response_schema=None):
                return {"actions": [{"action": "wait"}], "reason": "Thinking"}

        planner = Planner(MissingGoalLLM(), "test", 3)
        with self.assertRaisesRegex(ValueError, "Decision requires a goal"):
            planner.decide(
                {"inventory": {}, "nearby_blocks": []},
                {"mode": "free_play", "current_goal": None, "goal_status": "choose", "goal_progress": None},
                [],
                [],
            )
        self.assertEqual(planner.last_decision_keys, ["actions", "reason"])


if __name__ == "__main__":
    unittest.main()
