import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from agent.core.agent import AutonomousAgent
from agent.core.goals import GoalManager
from agent.core.planner import Planner
from agent.core.requirements import RequirementPlanner
from agent.memory.short_term import ShortTermMemory


BASE_STATE = {
    "connected": True,
    "position": {"x": 0, "y": 64, "z": 0},
    "health": 20,
    "food": 20,
    "inventory": {},
    "tools": [],
    "nearby_entities": [],
    "nearby_blocks": [],
}


def decision(action):
    return {
        "goal": "gather wood",
        "purpose": "obtain wood",
        "objective": {"type": "explore", "completion": "find wood"},
        "goal_status": "continue",
        "progress": "searching",
        "reason": "Continue resource gathering",
        "actions": [action],
        "memory": None,
    }


class ScriptedPlanner:
    last_decision_keys = []

    def __init__(self, decisions):
        self.decisions = decisions
        self.calls = 0

    def decide(self, *_args):
        item = self.decisions[min(self.calls, len(self.decisions) - 1)]
        self.calls += 1
        return item


class FakeMinecraft:
    def __init__(self, stop_after, result=None):
        self.stop_after = stop_after
        self.result = result or {"success": True, "action": "wait", "reason": "ok", "state_change": {}}
        self.state_calls = 0
        self.actions = []
        self.agent = None

    def state(self):
        self.state_calls += 1
        if self.state_calls > self.stop_after:
            self.agent.running = False
            return {"connected": False}
        return dict(BASE_STATE)

    def act(self, action):
        self.actions.append(action)
        result = dict(self.result)
        result["action"] = action["action"]
        return result


class EmptyLongMemory:
    def relevant(self):
        return []

    def add(self, _entry):
        pass


class TreeRequirementProvider:
    def __init__(self, tree):
        self.tree = tree

    def requirements(self, item, count=1):
        return {
            "schema": "requirement_plan.v1",
            "item": item,
            "count": count,
            "inventory": {},
            "tree": self.tree,
        }


class MissingTreeRequirementProvider:
    def requirements(self, item, count=1):
        return {
            "schema": "requirement_plan.v1",
            "item": item,
            "count": count,
            "inventory": {},
        }


def requirement_node(item, source="gather", ingredients=None, prerequisites=None, blocks=None):
    return {
        "item": item,
        "required": 1,
        "current": 0,
        "missing": 1,
        "source": source,
        "requires_table": False,
        "required_tools": [],
        "gather_blocks": blocks or [item],
        "prerequisites": prerequisites or [],
        "ingredients": ingredients or [],
    }


def pickaxe_requirement_tree():
    log = requirement_node("oak_log", blocks=["oak_log"])
    planks = requirement_node("oak_planks", "craft", [log])
    wooden = requirement_node("wooden_pickaxe", "craft", [planks])
    stone = requirement_node("stone_pickaxe", "craft", prerequisites=[wooden])
    ore = requirement_node("iron_ore", prerequisites=[stone], blocks=["iron_ore"])
    ingot = requirement_node("iron_ingot", "smelt", [ore])
    return requirement_node("iron_pickaxe", "craft", [ingot])


class RejectOnceRequirements:
    def __init__(self):
        self.guards = 0

    def assess(self, _objective, _state):
        return {"complete": False, "requirements": {}, "missing": {}, "progress_percent": 0, "subgoal": "follow strategic goal", "blockers": [], "nodes": [], "actionable": None, "completion_condition": {"type": "strategy"}}

    def guard_action(self, action, _assessment):
        self.guards += 1
        if self.guards == 1:
            raise ValueError("test rejection")
        return action

    def marker(self, assessment):
        return json.dumps(
            {
                "missing": assessment.get("missing", {}),
                "subgoal": assessment.get("subgoal"),
            },
            sort_keys=True,
            separators=(",", ":"),
        )


def make_agent(planner, minecraft, requirement_planner=None):
    goals = GoalManager("free_play")
    agent = AutonomousAgent(
        minecraft,
        planner,
        goals,
        ShortTermMemory(20),
        EmptyLongMemory(),
        {"decision_interval_seconds": 1, "consecutive_error_limit": 3},
        requirement_planner=requirement_planner,
    )
    minecraft.agent = agent
    return agent


class ExecutionFlowTests(unittest.TestCase):
    def run_agent(self, agent):
        output = io.StringIO()
        with patch("agent.core.agent.time.sleep", return_value=None), redirect_stdout(output):
            agent.run()
        return output.getvalue()

    def test_valid_planner_action_reaches_minecraft_client(self):
        class ValidLLM:
            def complete_json(self, _system, _user, response_schema=None):
                return decision({"action": "wait", "seconds": 1})

        minecraft = FakeMinecraft(stop_after=1)
        agent = make_agent(Planner(ValidLLM(), "system", 3), minecraft)
        output = self.run_agent(agent)
        self.assertEqual(len(minecraft.actions), 1)
        self.assertIn("[PLAN ACTIONS]", output)
        self.assertIn("[GUARD] action=wait", output)
        self.assertIn("[EXECUTE] action=wait", output)

    def test_guard_rejection_does_not_terminate_agent_loop(self):
        minecraft = FakeMinecraft(stop_after=2)
        requirements = RejectOnceRequirements()
        agent = make_agent(ScriptedPlanner([decision({"action": "wait"})]), minecraft, requirements)
        output = self.run_agent(agent)
        self.assertGreaterEqual(requirements.guards, 2)
        self.assertEqual(len(minecraft.actions), 1)
        self.assertIn("[REPLAN] Requirement validation rejected the action", output)
        rejected = agent.short_memory.summary()[0]["result"]
        self.assertIn("rejected_plan_actions", rejected["details"])
        self.assertEqual(rejected["details"]["current_goal"], "gather wood")

    def test_valid_prerequisite_action_reaches_execution(self):
        minecraft = FakeMinecraft(stop_after=1)
        requirements = RequirementPlanner(
            TreeRequirementProvider(pickaxe_requirement_tree())
        )
        agent = make_agent(
            ScriptedPlanner([decision({"action": "collect", "block": "oak_log"})]),
            minecraft,
            requirements,
        )
        agent.goals.update(
            "obtain_pickaxe",
            objective={"type": "obtain_item", "item": "iron_pickaxe", "count": 1},
        )

        self.run_agent(agent)

        self.assertEqual(
            minecraft.actions,
            [{"action": "collect", "block": "oak_log", "count": 1}],
        )

    def test_invalid_registry_alias_does_not_reach_execution(self):
        minecraft = FakeMinecraft(stop_after=1)
        requirements = RequirementPlanner(
            TreeRequirementProvider(pickaxe_requirement_tree())
        )
        agent = make_agent(
            ScriptedPlanner([decision({"action": "craft", "item": "planks"})]),
            minecraft,
            requirements,
        )
        agent.goals.update(
            "obtain_pickaxe",
            objective={"type": "obtain_item", "item": "iron_pickaxe", "count": 1},
        )

        output = self.run_agent(agent)

        self.assertIn("unnecessary_resource: planks", output)
        self.assertEqual(minecraft.actions, [])

    def test_replan_after_rejected_ore_does_not_crash(self):
        minecraft = FakeMinecraft(stop_after=2)
        requirements = RequirementPlanner(
            TreeRequirementProvider(pickaxe_requirement_tree())
        )
        planner = ScriptedPlanner(
            [
                decision({"action": "mine", "block": "iron_ore"}),
                decision({"action": "collect", "block": "oak_log"}),
            ]
        )
        agent = make_agent(planner, minecraft, requirements)
        agent.goals.update(
            "obtain_pickaxe",
            objective={"type": "obtain_item", "item": "iron_pickaxe", "count": 1},
        )

        output = self.run_agent(agent)

        self.assertIn("missing_prerequisite", output)
        self.assertIn("[REPLAN] Requirement validation rejected the action", output)
        self.assertEqual(
            minecraft.actions,
            [{"action": "collect", "block": "oak_log", "count": 1}],
        )

    def test_missing_requirement_tree_blocks_execution_without_crashing(self):
        minecraft = FakeMinecraft(stop_after=1)
        requirements = RequirementPlanner(MissingTreeRequirementProvider())
        agent = make_agent(
            ScriptedPlanner([decision({"action": "collect", "block": "oak_log"})]),
            minecraft,
            requirements,
        )
        agent.goals.update(
            "obtain_pickaxe",
            objective={"type": "obtain_item", "item": "iron_pickaxe", "count": 1},
        )

        output = self.run_agent(agent)

        self.assertIn("requirement_resolution_failed", output)
        self.assertEqual(minecraft.actions, [])

    def test_explore_discovery_is_reused_as_move_to(self):
        class FakeLLM:
            def complete_json(self, _system, _user, response_schema=None):
                return decision({"action": "explore", "targets": ["oak_log"], "radius": 30})

        planner = Planner(FakeLLM(), "test", 3)
        recent = [{
            "action": {"action": "explore", "targets": ["oak_log"]},
            "result": {"success": True, "state_change": {"discovered": {"type": "oak_log", "position": {"x": 20, "y": 64, "z": 4}}}},
        }]
        result = planner.decide(BASE_STATE, {"current_goal": "gather wood", "goal_status": "continue"}, recent, [])
        self.assertEqual(result["actions"][0]["action"], "move_to")
        self.assertEqual(result["actions"][0]["target"], {"x": 20, "y": 64, "z": 4})

    def test_repeated_failed_exploration_replans_without_infinite_loop(self):
        failed = {"success": False, "action": "explore", "reason": "noPath", "state_change": {}}
        minecraft = FakeMinecraft(stop_after=4, result=failed)
        requirements = RequirementPlanner(provider=None, loop_limit=2)
        agent = make_agent(ScriptedPlanner([decision({"action": "explore", "targets": ["oak_log"], "radius": 24})]), minecraft, requirements)
        output = self.run_agent(agent)
        self.assertGreaterEqual(agent.planner.calls, 4)
        self.assertLessEqual(len(minecraft.actions), 3)
        self.assertIn("plan_stalled", output)

    def test_agent_continues_deciding_after_successful_action(self):
        minecraft = FakeMinecraft(stop_after=2)
        planner = ScriptedPlanner([decision({"action": "wait"})])
        agent = make_agent(planner, minecraft)
        self.run_agent(agent)
        self.assertEqual(planner.calls, 2)
        self.assertEqual(len(minecraft.actions), 2)


if __name__ == "__main__":
    unittest.main()
