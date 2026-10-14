import json
import copy
import unittest
from unittest.mock import patch

from agent.core.planner import DECISION_SCHEMA, Planner
from agent.llm.client import LLMClient


VALID_DECISION = {
    "goal": "gather wood",
    "purpose": "obtain crafting materials",
    "objective": {"type": "obtain_item", "item": "oak_log", "count": 1},
    "goal_status": "continue",
    "progress": "searching for a tree",
    "reason": "wood is required",
    "actions": [{"action": "explore", "targets": ["oak_log"], "radius": 24}],
    "memory": None,
}


class CapturingLLM:
    def __init__(self, response=None):
        self.response = response or VALID_DECISION
        self.context = None
        self.schema = None

    def complete_json(self, _system, user, response_schema=None):
        self.context = json.loads(user.split("PLANNER_CONTEXT_JSON:\n", 1)[1])
        self.schema = response_schema
        return self.response


class FakeHTTPResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


class LLMContractTests(unittest.TestCase):
    def test_experience_guidance_is_input_context(self):
        llm = CapturingLLM()
        planner = Planner(llm, "system", 3)
        guidance = {"failed_experiences": [{"failure_reason": "noPath"}], "corrected_strategies": ["change direction"]}
        result = planner.decide(
            {"inventory": {}, "nearby_blocks": []},
            {"current_goal": None, "goal_status": "choose"},
            [], [], [], guidance,
        )
        self.assertEqual(result["goal"], "gather wood")
        self.assertEqual(llm.context["experience_guidance"], guidance)
        self.assertIs(llm.schema, DECISION_SCHEMA)

    def test_valid_llm_decision_is_accepted(self):
        planner = Planner(CapturingLLM(), "system", 3)
        result = planner.decide(
            {"inventory": {}, "nearby_blocks": []},
            {"current_goal": None, "goal_status": "choose"},
            [], [],
        )
        self.assertEqual(result["actions"][0]["action"], "explore")

    def test_experience_policy_object_is_rejected_as_decision(self):
        planner = Planner(CapturingLLM(), "system", 3)
        with self.assertRaisesRegex(ValueError, "Decision requires a goal"):
            planner.validate({"corrected_strategies": [], "failed_experiences": [], "rules": []})

    def test_planner_does_not_return_echoed_context(self):
        class EchoingLLM:
            def complete_json(self, _system, user, response_schema=None):
                return json.loads(user.split("PLANNER_CONTEXT_JSON:\n", 1)[1])

        planner = Planner(EchoingLLM(), "system", 3)
        with self.assertRaisesRegex(ValueError, "Decision requires a goal"):
            planner.decide(
                {"inventory": {}, "nearby_blocks": []},
                {"current_goal": None, "goal_status": "choose"},
                [], [],
                experience_guidance={"rules": ["example"]},
            )
        self.assertIn("experience_guidance", planner.last_decision_keys)

    def test_client_requests_json_schema_and_reads_assistant_content(self):
        captured = {}

        def fake_urlopen(request, timeout):
            captured["payload"] = json.loads(request.data.decode())
            return FakeHTTPResponse({"choices": [{"message": {"role": "assistant", "content": json.dumps(VALID_DECISION)}}]})

        client = LLMClient("http://127.0.0.1:11434/v1", "test-model")
        with patch("agent.llm.client.urlopen", side_effect=fake_urlopen):
            result = client.complete_json("system", "{}", response_schema=DECISION_SCHEMA)

        self.assertEqual(result["goal"], "gather wood")
        self.assertEqual(captured["payload"]["response_format"]["type"], "json_schema")
        self.assertEqual(captured["payload"]["response_format"]["json_schema"]["schema"], DECISION_SCHEMA)
        self.assertEqual(captured["payload"]["temperature"], 0)

    def test_rejected_old_plan_for_pickaxe_forces_different_replan(self):
        old_plan = copy.deepcopy(VALID_DECISION)
        old_plan.update({
            "goal": "obtain basic tools",
            "objective": {"type": "obtain_item", "item": "oak_log", "count": 1},
            "reason": "repeat the old basic tools plan",
            "actions": [
                {"action": "collect", "block": "oak_log", "count": 1},
                {"action": "craft", "item": "stick", "count": 2},
            ],
        })
        corrected = copy.deepcopy(VALID_DECISION)
        corrected.update({
            "goal": "obtain_pickaxe",
            "objective": {"type": "obtain_item", "item": "wooden_pickaxe", "count": 1},
            "reason": "follow the current deterministic subgoal",
            "actions": [{"action": "craft", "item": "wooden_pickaxe", "count": 1}],
        })

        class SequenceLLM:
            def __init__(self):
                self.responses = [old_plan, corrected]
                self.requests = []

            def complete_json(self, _system, user, response_schema=None):
                self.requests.append(user)
                return self.responses.pop(0)

        goals = {
            "current_goal": "obtain_pickaxe",
            "goal_status": "continue",
            "goal_objective": {"type": "obtain_item", "item": "wooden_pickaxe", "count": 1},
            "goal_purpose": "obtain a pickaxe",
            "goal_requirements": {"wooden_pickaxe": {"required": 1, "current": 0, "missing": 1}},
            "missing_resources": {"wooden_pickaxe": 1},
            "current_subgoal": "craft wooden_pickaxe x1",
            "next_requirement": {"item": "wooden_pickaxe", "missing": 1, "source": "craft"},
            "blockers": [],
            "completion_condition": {"type": "inventory_at_least", "item": "wooden_pickaxe", "count": 1},
        }
        requirement_signature = json.dumps(
            {"missing": goals["missing_resources"], "subgoal": goals["current_subgoal"]},
            sort_keys=True,
            separators=(",", ":"),
        )
        recent = [{
            "action": old_plan["actions"][0],
            "result": {
                "success": False,
                "reason": "unnecessary_resource: oak_log is not missing for the active goal",
                "details": {
                    "current_goal": "obtain_pickaxe",
                    "requirement_signature": requirement_signature,
                    "rejected_action": old_plan["actions"][0],
                    "rejected_plan_actions": old_plan["actions"],
                },
            },
        }]
        llm = SequenceLLM()
        planner = Planner(llm, "system", 3)
        result = planner.decide(
            {"inventory": {}, "nearby_blocks": [{"type": "oak_log", "position": {"x": 2, "y": 64, "z": 2}}]},
            goals,
            recent,
            [],
            experience_guidance={"successful_experiences": [{"goal": "obtain basic tools", "plan": old_plan["actions"]}]},
        )
        self.assertEqual(len(llm.requests), 2)
        self.assertIn("REPLAN_CORRECTION", llm.requests[1])
        self.assertEqual(result["goal"], "obtain_pickaxe")
        self.assertEqual(result["actions"][0]["item"], "wooden_pickaxe")
        first_context = json.loads(llm.requests[0].split("PLANNER_CONTEXT_JSON:\n", 1)[1])
        self.assertEqual(first_context["authoritative_goal"]["current_goal"], "obtain_pickaxe")
        self.assertEqual(first_context["authoritative_goal"]["next_requirement"]["item"], "wooden_pickaxe")

    def test_second_identical_rejected_plan_is_not_accepted(self):
        repeated = copy.deepcopy(VALID_DECISION)
        repeated["actions"] = [{"action": "collect", "block": "oak_log", "count": 1}]

        class RepeatingLLM:
            def complete_json(self, _system, _user, response_schema=None):
                return copy.deepcopy(repeated)

        goals = {
            "current_goal": "obtain_pickaxe",
            "missing_resources": {"wooden_pickaxe": 1},
            "current_subgoal": "craft wooden_pickaxe x1",
        }
        signature = json.dumps(
            {"missing": goals["missing_resources"], "subgoal": goals["current_subgoal"]},
            sort_keys=True,
            separators=(",", ":"),
        )
        recent = [{"result": {"reason": "unnecessary_resource", "details": {
            "current_goal": "obtain_pickaxe",
            "requirement_signature": signature,
            "rejected_action": repeated["actions"][0],
            "rejected_plan_actions": repeated["actions"],
        }}}]
        with self.assertRaisesRegex(ValueError, "repeated a deterministically rejected plan"):
            Planner(RepeatingLLM(), "system", 3).decide(
                {"inventory": {}, "nearby_blocks": [{"type": "oak_log"}]}, goals, recent, []
            )

    def test_old_goal_cannot_replace_active_deterministic_goal(self):
        planner = Planner(CapturingLLM(), "system", 3)
        candidate = copy.deepcopy(VALID_DECISION)
        candidate.update({"goal": "obtain basic tools", "goal_status": "replace"})
        result = planner.validate(
            candidate,
            {"health": 20, "food": 20, "inventory": {}, "nearby_blocks": []},
            {"current_goal": "obtain_pickaxe", "missing_resources": {"wooden_pickaxe": 1}, "blockers": []},
        )
        self.assertEqual(result["goal_status"], "continue")
        self.assertEqual(result["goal"], "obtain_pickaxe")


if __name__ == "__main__":
    unittest.main()
