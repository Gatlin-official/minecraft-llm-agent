import json
from collections import deque


REQUIREMENT_PLAN_SCHEMA = "requirement_plan.v1"


def _node_dependencies(node):
    """Return every deterministic dependency in prerequisite order."""
    return [
        *node.get("prerequisites", []),
        *node.get("ingredients", []),
    ]


def suggest_free_play_goals(state):
    """Return compact state-based choices for the LLM, not a fixed progression."""
    inventory = state.get("inventory", {})
    tools = set(state.get("tools", []))

    choices = []

    # No tools -> basic tools should be the first useful objective.
    if not tools:
        choices.append("obtain basic tools")

    # Low food -> prioritize food.
    food = state.get("food")
    if isinstance(food, (int, float)) and food < 14:
        choices.append("obtain food")

    # No wood -> gather wood.
    if not any(name.endswith("_log") for name in inventory):
        choices.append("gather wood")

    # Stone tools -> next logical progression.
    if any(name.startswith("stone_") for name in tools):
        choices.append("improve equipment or obtain iron")

    # Open-ended goals.
    choices.extend([
        "explore for a useful location",
        "build or improve shelter",
    ])

    return list(dict.fromkeys(choices))[:5]


class ActionLoopDetector:
    def __init__(self, limit=3):
        self.limit = limit
        self.history = deque(maxlen=limit)

    @staticmethod
    def signature(action):
        compact = {
            key: action.get(key)
            for key in (
                "action",
                "block",
                "item",
                "entity",
                "target",
            )
            if action.get(key) is not None
        }
        return json.dumps(
            compact,
            sort_keys=True,
            separators=(",", ":"),
        )

    def observe(self, action, progress_marker):
        self.history.append(
            (
                self.signature(action),
                progress_marker,
            )
        )

    def is_stalled(self, action, progress_marker):
        if len(self.history) < self.limit:
            return False

        signature = self.signature(action)

        return all(
            old_signature == signature
            and old_progress == progress_marker
            for old_signature, old_progress in self.history
        )

    def reset(self):
        self.history.clear()


class RequirementPlanner:

    def __init__(self, provider, loop_limit=3):
        self.provider = provider
        self.loops = ActionLoopDetector(loop_limit)

    # ---------------------------------------------------------
    # REQUIREMENT ASSESSMENT
    # ---------------------------------------------------------

    def assess(self, objective, state):

        # No structured objective.
        if not isinstance(objective, dict):
            return {
                "completion_condition": {
                    "type": "strategy"
                },
                "complete": False,
                "requirements": {},
                "missing": {},
                "progress_percent": 0,
                "subgoal": "follow strategic goal",
                "blockers": [],
                "nodes": [],
                "actionable": None,
            }

        objective_type = objective.get("type")

        # Strategic/open-ended objectives such as exploration.
        if objective_type != "obtain_item":
            return {
                "completion_condition": objective,
                "complete": False,
                "requirements": {},
                "missing": {},
                "progress_percent": 0,
                "subgoal": self._strategic_subgoal(objective),
                "blockers": [],
                "nodes": [],
                "actionable": None,
            }

        # -----------------------------------------------------
        # STRUCTURED ITEM REQUIREMENT
        # -----------------------------------------------------

        item = objective.get("item")

        if not isinstance(item, str) or not item:
            return {
                "completion_condition": objective,
                "complete": False,
                "requirements": {},
                "missing": {},
                "progress_percent": 0,
                "subgoal": "determine required item",
                "blockers": [
                    {
                        "reason": "invalid_objective_item"
                    }
                ],
                "nodes": [],
                "actionable": None,
            }

        try:
            count = max(
                1,
                int(objective.get("count", 1))
            )
        except (TypeError, ValueError):
            count = 1

        # Ask Mineflayer for the actual recipe/dependency tree.
        plan = self.provider.requirements(
            item,
            count,
        )

        root = self._plan_root(plan, item, count)

        nodes = []

        def walk(node):
            if not isinstance(node, dict):
                return

            nodes.append(node)

            for child in _node_dependencies(node):
                walk(child)

        walk(root)

        requirements = {}
        current = {}
        missing = {}

        for node in nodes:

            name = node.get("item")

            if not name:
                continue

            required = int(
                node.get("required", 0)
            )

            current_amount = int(
                node.get("current", 0)
            )

            missing_amount = int(
                node.get("missing", 0)
            )

            requirements[name] = (
                requirements.get(name, 0)
                + required
            )

            current[name] = (
                current.get(name, 0)
                + current_amount
            )

            missing[name] = (
                missing.get(name, 0)
                + missing_amount
            )

        missing = {
            name: amount
            for name, amount in missing.items()
            if amount > 0
        }

        total = sum(
            requirements.values()
        ) or count

        satisfied = sum(
            min(
                current.get(name, 0),
                amount,
            )
            for name, amount in requirements.items()
        )

        actionable = self._first_actionable(
            root
        )

        blockers = self._blockers(
            actionable,
            state,
        )

        return {
            "completion_condition": {
                "type": "inventory_at_least",
                "item": item,
                "count": count,
            },

            "complete": (
                int(
                    root.get(
                        "missing",
                        count,
                    )
                ) == 0
            ),

            "requirements": {
                name: {
                    "required": amount,
                    "current": current.get(
                        name,
                        0,
                    ),
                    "missing": max(
                        0,
                        amount
                        - current.get(
                            name,
                            0,
                        ),
                    ),
                }
                for name, amount
                in requirements.items()
            },

            "missing": missing,

            "progress_percent": min(
                100,
                round(
                    (
                        satisfied
                        / total
                    )
                    * 100
                ),
            ),

            "subgoal": self._subgoal(
                actionable
            ),

            "blockers": blockers,

            "nodes": nodes,

            "actionable": actionable,
        }

    @staticmethod
    def _plan_root(plan, item, count):
        """Validate the Node/Python requirement-plan boundary."""
        if not isinstance(plan, dict):
            raise ValueError(
                "invalid_requirement_plan: response must be an object"
            )

        if plan.get("success") is False or plan.get("error"):
            raise RuntimeError(
                "requirement_resolution_failed: "
                f"{plan.get('error') or 'unknown provider error'}"
            )

        if plan.get("schema") != REQUIREMENT_PLAN_SCHEMA:
            raise ValueError(
                "invalid_requirement_plan: expected schema "
                f"{REQUIREMENT_PLAN_SCHEMA}"
            )

        if plan.get("item") != item or plan.get("count") != count:
            raise ValueError(
                "invalid_requirement_plan: response target does not match request"
            )

        root = plan.get("tree")
        if not isinstance(root, dict):
            raise ValueError(
                "invalid_requirement_plan: tree must be an object"
            )

        return root

    # ---------------------------------------------------------
    # FIND NEXT REQUIRED NODE
    # ---------------------------------------------------------

    def _first_actionable(self, node):

        if not node:
            return None

        if not node.get("missing"):
            return None

        # Always solve children first.
        for child in _node_dependencies(node):

            actionable = self._first_actionable(
                child
            )

            if actionable:
                return actionable

        return node

    # ---------------------------------------------------------
    # SUBGOAL DESCRIPTION
    # ---------------------------------------------------------

    @staticmethod
    def _subgoal(node):

        if not node:
            return "goal complete"

        if node.get("source") == "craft":
            verb = "craft"
        else:
            verb = "obtain"

        return (
            f"{verb} "
            f"{node.get('item')} "
            f"x{node.get('missing', 0)}"
        )

    @staticmethod
    def _strategic_subgoal(objective):

        if not isinstance(
            objective,
            dict,
        ):
            return "follow strategic goal"

        objective_type = objective.get(
            "type"
        )

        if objective_type == "explore":
            return "explore a new area"

        if objective_type == "build":
            return "build or improve structure"

        if objective_type == "survive":
            return "maintain survival needs"

        if objective_type == "strategy":
            return objective.get(
                "completion",
                "follow strategic goal",
            )

        return "follow strategic goal"

    # ---------------------------------------------------------
    # PREREQUISITE CHECKING
    # ---------------------------------------------------------

    @staticmethod
    def _blockers(node, state):

        if not node:
            return []

        blockers = []

        inventory = state.get(
            "inventory",
            {},
        )

        # Required tool.
        required_tools = node.get(
            "required_tools",
            [],
        )

        if (
            required_tools
            and not any(
                inventory.get(tool, 0) > 0
                for tool in required_tools
            )
        ):
            blockers.append(
                {
                    "reason": "requires_tool",
                    "acceptable_tools": required_tools,
                }
            )

        # Crafting table.
        if node.get(
            "requires_table"
        ):

            nearby = {
                block.get("type")
                for block
                in state.get(
                    "nearby_blocks",
                    [],
                )
            }

            if "crafting_table" not in nearby:

                blockers.append(
                    {
                        "reason": "requires_crafting_table",
                        "in_inventory": (
                            inventory.get(
                                "crafting_table",
                                0,
                            )
                            > 0
                        ),
                    }
                )

        return blockers

    # ---------------------------------------------------------
    # PROGRESS MARKER
    # ---------------------------------------------------------

    @staticmethod
    def marker(assessment):

        return json.dumps(
            {
                "missing": assessment.get(
                    "missing",
                    {},
                ),

                "subgoal": assessment.get(
                    "subgoal"
                ),
            },

            sort_keys=True,
            separators=(",", ":"),
        )

    # ---------------------------------------------------------
    # ACTION GUARD
    # ---------------------------------------------------------

    def guard_action(self, action, assessment):
        name = action.get("action")

        # Actions that can legitimately get stuck in a loop.
        loop_actions = {
            "collect",
            "mine",
            "craft",
            "smelt",
            "explore",
            "move_to",
        }

        marker = self.marker(assessment)

        if name in loop_actions:
            if self.loops.is_stalled(action, marker):
                # Clear the old loop so the next strategy gets
                # a clean history.
                self.loops.reset()

                raise ValueError(
                    "plan_stalled: repeated action without progress"
                )

            # Record this attempt immediately.
            self.loops.observe(action, marker)

        target = (
            action.get("block")
            if name in {"collect", "mine"}
            else action.get("item")
            if name in {"craft", "smelt"}
            else None
        )

        blockers = assessment.get("blockers", [])

        if any(
            blocker.get("reason") == "requirement_resolution_failed"
            for blocker in blockers
        ):
            raise ValueError(
                "requirement_resolution_failed: deterministic plan unavailable"
            )

        # Tool prerequisite
        if name in {"collect", "mine"} and any(
            blocker.get("reason") == "requires_tool"
            for blocker in blockers
        ):
            raise ValueError(
                "missing_prerequisite: obtain a suitable tool "
                "before mining this requirement"
            )

        # Crafting-table prerequisite
        if name == "craft" and any(
            blocker.get("reason") == "requires_crafting_table"
            for blocker in blockers
        ):
            raise ValueError(
                "missing_prerequisite: place a crafting table "
                "before crafting this requirement"
            )

        # Prevent unnecessary resource collection.
        if (
            target
            and assessment.get("nodes")
            and name in {"collect", "mine", "craft", "smelt"}
        ):
            matching = []

            for node in assessment.get("nodes", []):
                if (
                    node.get("item") == target
                    or target in node.get("gather_blocks", [])
                ):
                    matching.append(node)

            required_missing = sum(
                int(node.get("missing", 0))
                for node in matching
            )

            if not matching or required_missing <= 0:
                raise ValueError(
                    f"unnecessary_resource: {target} "
                    "is not missing for the active goal"
                )

            if all(
                any(
                    int(dependency.get("missing", 0)) > 0
                    for dependency in _node_dependencies(node)
                )
                for node in matching
            ):
                raise ValueError(
                    f"missing_prerequisite: resolve dependencies before {target}"
                )

            action = dict(action)

            action["count"] = min(
                max(1, int(action.get("count", 1))),
                required_missing,
            )

        return action

    # ---------------------------------------------------------
    # RECORD EXECUTED ACTION
    # ---------------------------------------------------------

    def observe(
        self,
        action,
        assessment,
    ):

        marker = self.marker(
            assessment
        )

        self.loops.observe(
            action,
            marker,
        )
