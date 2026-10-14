import json
import tempfile
import unittest
from pathlib import Path

from agent.core.hierarchy import HierarchicalPlanner
from agent.core.world_model import WorldModel
from agent.memory.semantic import SemanticMemory
from agent.memory.skills import SkillLibrary
from agent.memory.learner import ContinualLearner


class ContinualLearningTests(unittest.TestCase):
    def test_semantic_memory_retrieves_relevant_fact(self):
        with tempfile.TemporaryDirectory() as d:
            memory = SemanticMemory(Path(d) / "semantic.json")
            memory.learn("Mining oak_log failed because the target was out of reach", context="gather wood")
            result = memory.retrieve("gather wood oak_log", limit=1)
            self.assertEqual(len(result), 1)
            self.assertIn("oak_log", result[0]["fact"])

    def test_skill_library_consolidates_successful_sequence(self):
        with tempfile.TemporaryDirectory() as d:
            skills = SkillLibrary(Path(d) / "skills.json")
            skills.upsert(
                goal="make a wooden pickaxe",
                objective={"type": "obtain_item", "item": "wooden_pickaxe", "count": 1},
                actions=[
                    {"action": "collect", "block": "oak_log", "count": 1},
                    {"action": "craft", "item": "wooden_pickaxe", "count": 1},
                ],
            )
            result = skills.retrieve("make a wooden pickaxe", {"item": "wooden_pickaxe"})
            self.assertEqual(result[0]["actions"][0]["action"], "collect")

    def test_learner_turns_successful_actions_into_skill(self):
        with tempfile.TemporaryDirectory() as d:
            semantic = SemanticMemory(Path(d) / "semantic.json")
            skills = SkillLibrary(Path(d) / "skills.json")
            learner = ContinualLearner(semantic, skills)
            objective = {"type": "obtain_item", "item": "wooden_pickaxe", "count": 1}
            learner.observe(
                state_before={},
                goal="make a wooden pickaxe",
                objective=objective,
                action={"action": "collect", "block": "oak_log"},
                result={"success": True},
                reward=1,
            )
            learner.observe(
                state_before={},
                goal="make a wooden pickaxe",
                objective=objective,
                action={"action": "craft", "item": "wooden_pickaxe"},
                result={"success": True},
                reward=2,
            )
            self.assertTrue(skills.retrieve("make a wooden pickaxe"))

    def test_hierarchy_preserves_authoritative_goal(self):
        hierarchy = HierarchicalPlanner()
        goals = {
            "current_goal": "make a wooden pickaxe",
            "goal_purpose": "basic tool",
            "goal_objective": {"type": "obtain_item", "item": "wooden_pickaxe", "count": 1},
            "current_subgoal": "obtain oak_log x1",
            "next_requirement": {
                "item": "oak_log",
                "missing": 1,
                "source": "gather",
                "gather_blocks": ["oak_log"],
            },
            "blockers": [],
            "goal_progress": {"percent": 0},
        }
        result = hierarchy.build(goals)
        self.assertEqual(result["high_level_goal"], "make a wooden pickaxe")
        self.assertEqual(result["subgoal"], "obtain oak_log x1")
        self.assertIn("oak_log", result["action_intent"])

    def test_world_model_tracks_regions_and_risk(self):
        world = WorldModel()
        result = world.observe({
            "position": {"x": 20, "y": 64, "z": 4},
            "health": 8,
            "food": 20,
            "nearby_entities": [{"type": "zombie", "hostile": True, "distance": 5}],
            "nearby_blocks": [],
        })
        self.assertTrue(result["risk"]["danger"])
        self.assertEqual(result["exploration"]["current_region"], (1, 0))


if __name__ == "__main__":
    unittest.main()
