import copy
import tempfile
import unittest
from pathlib import Path

from agent.core.goals import GoalManager
from agent.core.requirements import (
    RequirementPlanner,
    suggest_free_play_goals,
)
from agent.memory.experience_policy import ExperiencePolicy
from agent.memory.experience import (
    ExperienceEvaluator,
    ExperienceStore,
)

    

class FakeProvider:
    def __init__(self, tree):
        self.tree = tree

    def requirements(self, item, count=1):
        return {
            "schema": "requirement_plan.v1",
            "item": item,
            "count": count,
            "inventory": {},
            "tree": copy.deepcopy(self.tree),
        }


class MissingTreeProvider:
    def requirements(self, item, count=1):
        return {
            "schema": "requirement_plan.v1",
            "item": item,
            "count": count,
            "inventory": {},
        }


def node(
    item,
    required,
    current=0,
    source="gather",
    ingredients=None,
    tools=None,
    blocks=None,
):
    return {
        "item": item,
        "required": required,
        "current": current,
        "missing": max(0, required - current),
        "source": source,
        "requires_table": (
            source == "craft"
            and item.endswith("_axe")
        ),
        "required_tools": tools or [],
        "gather_blocks": blocks or [item],
        "ingredients": ingredients or [],
    }


def axe_tree():
    return node(
        "wooden_axe",
        1,
        source="craft",
        ingredients=[
            node(
                "spruce_planks",
                3,
                source="craft",
                ingredients=[
                    node("spruce_log", 1)
                ],
            ),
            node(
                "stick",
                2,
                source="craft",
                ingredients=[
                    node(
                        "spruce_planks",
                        2,
                        source="craft",
                        ingredients=[
                            node("spruce_log", 1)
                        ],
                    )
                ],
            ),
        ],
    )


class DecisionLayerTests(unittest.TestCase):

    # =========================================================
    # REQUIREMENT PLANNER
    # =========================================================

    def test_resource_requirement_calculation(self):
        assessment = RequirementPlanner(
            FakeProvider(axe_tree())
        ).assess(
            {
                "type": "obtain_item",
                "item": "wooden_axe",
                "count": 1,
            },
            {
                "inventory": {},
                "nearby_blocks": [],
            },
        )

        self.assertEqual(
            assessment["requirements"]["spruce_planks"]["required"],
            5,
        )

        self.assertEqual(
            assessment["missing"]["spruce_log"],
            2,
        )

    def test_requirement_plan_without_tree_is_rejected_by_schema(self):
        planner = RequirementPlanner(MissingTreeProvider())

        with self.assertRaisesRegex(
            ValueError,
            "invalid_requirement_plan: tree must be an object",
        ):
            planner.assess(
                {
                    "type": "obtain_item",
                    "item": "wooden_pickaxe",
                    "count": 1,
                },
                {"inventory": {}},
            )

    def test_prerequisite_chain_selects_deepest_actionable_resource(self):
        wooden_pickaxe = node(
            "wooden_pickaxe",
            1,
            source="craft",
            ingredients=[
                node(
                    "oak_planks",
                    3,
                    source="craft",
                    ingredients=[node("oak_log", 1, blocks=["oak_log"])],
                ),
                node("stick", 2, source="craft"),
            ],
        )
        stone_pickaxe = node(
            "stone_pickaxe",
            1,
            source="craft",
            ingredients=[node("cobblestone", 3)],
        )
        stone_pickaxe["prerequisites"] = [wooden_pickaxe]
        iron_ore = node(
            "iron_ore",
            3,
            tools=["stone_pickaxe", "iron_pickaxe"],
            blocks=["iron_ore"],
        )
        iron_ore["prerequisites"] = [stone_pickaxe]
        tree = node(
            "iron_pickaxe",
            1,
            source="craft",
            ingredients=[
                node(
                    "iron_ingot",
                    3,
                    source="smelt",
                    ingredients=[iron_ore],
                ),
                node("stick", 2, source="craft"),
            ],
        )

        assessment = RequirementPlanner(FakeProvider(tree)).assess(
            {
                "type": "obtain_item",
                "item": "iron_pickaxe",
                "count": 1,
            },
            {"inventory": {}, "nearby_blocks": []},
        )

        self.assertEqual(assessment["actionable"]["item"], "oak_log")
        self.assertEqual(assessment["subgoal"], "obtain oak_log x1")
        self.assertIn("iron_ore", assessment["requirements"])
        self.assertIn("wooden_pickaxe", assessment["requirements"])

        with self.assertRaisesRegex(ValueError, "missing_prerequisite"):
            RequirementPlanner(FakeProvider(tree)).guard_action(
                {"action": "mine", "block": "iron_ore", "count": 1},
                assessment,
            )

    def test_invalid_registry_alias_is_rejected_by_requirements(self):
        assessment = RequirementPlanner(FakeProvider(axe_tree())).assess(
            {
                "type": "obtain_item",
                "item": "wooden_axe",
                "count": 1,
            },
            {"inventory": {}, "nearby_blocks": []},
        )

        with self.assertRaisesRegex(ValueError, "unnecessary_resource: planks"):
            RequirementPlanner(FakeProvider(axe_tree())).guard_action(
                {"action": "craft", "item": "planks", "count": 1},
                assessment,
            )

    def test_inventory_satisfies_requirement(self):
        planner = RequirementPlanner(
            FakeProvider(
                node(
                    "wooden_axe",
                    1,
                    current=1,
                )
            )
        )

        assessment = planner.assess(
            {
                "type": "obtain_item",
                "item": "wooden_axe",
                "count": 1,
            },
            {},
        )

        self.assertTrue(
            assessment["complete"]
        )

        self.assertEqual(
            assessment["missing"],
            {},
        )

    def test_missing_prerequisite_detection(self):
        tree = node(
            "cobblestone",
            3,
            tools=[
                "wooden_pickaxe",
                "stone_pickaxe",
            ],
            blocks=["stone"],
        )

        assessment = RequirementPlanner(
            FakeProvider(tree)
        ).assess(
            {
                "type": "obtain_item",
                "item": "cobblestone",
                "count": 3,
            },
            {
                "inventory": {},
                "nearby_blocks": [
                    {
                        "type": "stone"
                    }
                ],
            },
        )

        self.assertEqual(
            assessment["blockers"][0]["reason"],
            "requires_tool",
        )

    # =========================================================
    # GOALS
    # =========================================================

    def test_goal_completion(self):
        goals = GoalManager()

        goals.update(
            "Craft wooden axe",
            objective={
                "type": "obtain_item",
                "item": "wooden_axe",
                "count": 1,
            },
        )

        goals.apply_assessment(
            {
                "requirements": {},
                "missing": {},
                "progress_percent": 100,
                "subgoal": "goal complete",
                "blockers": [],
                "completion_condition": {
                    "item": "wooden_axe"
                },
            }
        )

        goals.complete_active()

        self.assertIsNone(
            goals.current
        )

        self.assertIn(
            "Craft wooden axe",
            goals.completed,
        )

    # =========================================================
    # LOOP DETECTION
    # =========================================================

    def test_repeated_action_loop_detection(self):
        planner = RequirementPlanner(
            FakeProvider(
                node("map", 1)
            )
        )

        assessment = planner.assess(
            {
                "type": "strategy"
            },
            {},
        )

        action = {
            "action": "explore",
            "radius": 24,
        }

        for _ in range(3):
            planner.observe(
                action,
                assessment,
            )

        with self.assertRaisesRegex(
            ValueError,
            "plan_stalled",
        ):
            planner.guard_action(
                action,
                assessment,
            )

    # =========================================================
    # EXPERIENCE STORE
    # =========================================================

    def test_failed_action_creates_experience(self):
        with tempfile.TemporaryDirectory() as directory:

            store = ExperienceStore(
                Path(directory)
                / "experiences.json"
            )

            store.add(
                state={
                    "inventory": {}
                },
                goal="Mine stone",
                reasoning="Need stone",
                plan={
                    "action": "mine",
                    "block": "stone",
                },
                result={
                    "success": False,
                    "reason": "requires_pickaxe",
                },
                corrected_strategy=(
                    "Obtain a pickaxe first."
                ),
                reward=-2,
            )

            self.assertEqual(
                store.records[0][
                    "failure_reason"
                ],
                "requires_pickaxe",
            )

            self.assertEqual(
                store.records[0][
                    "corrected_strategy"
                ],
                "Obtain a pickaxe first.",
            )

    def test_relevant_experience_retrieval(self):
        with tempfile.TemporaryDirectory() as directory:

            store = ExperienceStore(
                Path(directory)
                / "experiences.json"
            )

            store.add(
                state={},
                goal="Mine stone",
                reasoning="Need stone",
                plan={
                    "action": "mine",
                    "block": "stone",
                },
                result={
                    "success": False,
                    "reason": "requires_pickaxe",
                },
                corrected_strategy=(
                    "Obtain a pickaxe first."
                ),
                reward=-2,
            )

            store.add(
                state={},
                goal="Find food",
                reasoning="Hungry",
                plan={
                    "action": "explore"
                },
                result={
                    "success": True
                },
                reward=1,
            )

            results = store.retrieve(
                "stone pickaxe",
                limit=1,
            )

            self.assertEqual(
                results[0][
                    "failure_reason"
                ],
                "requires_pickaxe",
            )

    # =========================================================
    # NEW: EXPERIENCE SIMILARITY
    # =========================================================

    def test_experience_retrieval_prefers_similar_success(
        self
    ):
        with tempfile.TemporaryDirectory() as directory:

            store = ExperienceStore(
                Path(directory)
                / "experiences.json"
            )

            # ---------------------------------------------
            # Relevant successful experience
            # ---------------------------------------------

            store.add(
                state={
                    "inventory": {
                        "wooden_pickaxe": 1
                    },
                    "tools": [
                        "wooden_pickaxe"
                    ],
                },
                goal="Mine stone",
                reasoning=(
                    "Use a pickaxe to mine stone"
                ),
                plan={
                    "action": "mine",
                    "block": "stone",
                },
                result={
                    "success": True,
                    "reason": "stone mined",
                },
                reward=2,
            )

            # ---------------------------------------------
            # Unrelated successful experience
            # ---------------------------------------------

            store.add(
                state={
                    "inventory": {},
                    "tools": [],
                },
                goal="Find food",
                reasoning="Need food",
                plan={
                    "action": "explore",
                },
                result={
                    "success": True,
                },
                reward=1,
            )

            results = store.retrieve(
                "mine stone wooden pickaxe",
                limit=1,
            )

            self.assertEqual(
                len(results),
                1,
            )

            self.assertEqual(
                results[0]["goal"],
                "Mine stone",
            )

            self.assertEqual(
                results[0][
                    "selected_plan"
                ]["action"],
                "mine",
            )

    # =========================================================
    # FREE PLAY
    # =========================================================

    def test_free_play_goal_selection_options_follow_state(
        self
    ):
        choices = suggest_free_play_goals(
            {
                "inventory": {},
                "tools": [],
                "food": 8,
            }
        )

        self.assertIn(
            "obtain basic tools",
            choices,
        )

        self.assertIn(
            "obtain food",
            choices,
        )

        self.assertIn(
            "gather wood",
            choices,
        )

    # =========================================================
    # RECOVERY
    # =========================================================

    def test_planner_recovery_strategy_after_failure(
        self
    ):
        evaluator = ExperienceEvaluator()

        strategy = evaluator.corrected_strategy(
            {
                "action": "mine",
                "block": "stone",
            },
            {
                "success": False,
                "reason": "requires_tool",
            },
        )

        self.assertIn(
            "Obtain a suitable tool",
            strategy,
        )

    # =========================================================
    # UNNECESSARY COLLECTION
    # =========================================================

    def test_no_unnecessary_resource_collection(
        self
    ):
        planner = RequirementPlanner(
            FakeProvider(
                node(
                    "spruce_log",
                    1,
                    current=1,
                )
            )
        )

        assessment = planner.assess(
            {
                "type": "obtain_item",
                "item": "spruce_log",
                "count": 1,
            },
            {},
        )

        with self.assertRaisesRegex(
            ValueError,
            "unnecessary_resource",
        ):
            planner.guard_action(
                {
                    "action": "collect",
                    "block": "spruce_log",
                    "count": 1,
                },
                assessment,
            )
    def test_experience_policy_avoids_failed_action(self):
        experiences = [
            {
                "selected_plan": {
                    "action": "mine",
                    "block": "stone",
                },
                "success": False,
                "failure_reason": "requires_pickaxe",
                "corrected_strategy": "Obtain a pickaxe first.",
                "reward": -2,
            }
        ]

        policy = ExperiencePolicy(None)

        action = {
            "action": "mine",
            "block": "stone",
        }

        self.assertTrue(
            policy.should_avoid(
                action,
                experiences,
            )
        )

    def test_experience_policy_prefers_successful_action(self):
        experiences = [
            {
                "selected_plan": {
                    "action": "collect",
                    "block": "spruce_log",
                },
                "success": True,
                "reward": 1,
            }
        ]

        policy = ExperiencePolicy(None)

        action = {
            "action": "collect",
            "block": "spruce_log",
        }

        self.assertTrue(
            policy.should_prefer(
                action,
                experiences,
            )
        )

    def test_experience_policy_extracts_correction(self):
        experiences = [
            {
                "selected_plan": {
                    "action": "mine",
                    "block": "stone",
                },
                "success": False,
                "failure_reason": "requires_pickaxe",
                "corrected_strategy": "Obtain a pickaxe first.",
                "reward": -2,
            }
        ]

        policy = ExperiencePolicy(None)

        guidance = policy.guidance(
            experiences
        )

        self.assertIn(
            "Obtain a pickaxe first.",
            guidance["corrected_strategies"],
        )

    def test_experience_policy_does_not_avoid_successful_action(self):
        experiences = [
            {
                "selected_plan": {
                    "action": "collect",
                    "block": "oak_log",
                },
                "success": True,
                "reward": 1,
            }
        ]

        policy = ExperiencePolicy(None)

        action = {
            "action": "collect",
            "block": "oak_log",
        }

        self.assertFalse(
            policy.should_avoid(
                action,
                experiences,
            )
        )

if __name__ == "__main__":
    unittest.main()
