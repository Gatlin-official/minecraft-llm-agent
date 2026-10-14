import json

from .requirements import suggest_free_play_goals


ALLOWED_ACTIONS = {
    "move_to",
    "explore",
    "collect",
    "mine",
    "place_block",
    "craft",
    "smelt",
    "attack",
    "eat",
    "equip",
    "sleep",
    "look_at",
    "open_chest",
    "deposit_item",
    "withdraw_item",
    "wait",
}


DECISION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "goal": {"type": "string", "minLength": 1},
        "purpose": {"type": "string"},
        "objective": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "type": {"type": "string", "enum": ["obtain_item", "explore", "survive", "build", "strategy"]},
                "item": {"type": "string"},
                "count": {"type": "integer", "minimum": 1},
                "completion": {"type": "string"},
            },
            "required": ["type"],
        },
        "goal_status": {"type": "string", "enum": ["continue", "complete", "replace"]},
        "progress": {"type": "string"},
        "reason": {"type": "string"},
        "actions": {
            "type": "array",
            "minItems": 1,
            "maxItems": 3,
            "items": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": sorted(ALLOWED_ACTIONS)},
                    "block": {"type": "string"},
                    "item": {"type": "string"},
                    "entity": {"type": "string"},
                    "count": {"type": "integer", "minimum": 1},
                    "radius": {"type": "number"},
                    "range": {"type": "number"},
                    "targets": {"type": "array", "items": {"type": "string"}},
                    "target": {"type": "object"},
                    "position": {"type": "object"},
                    "destination": {"type": "string"},
                    "container": {"type": "string"},
                    "fuel": {"type": "string"},
                    "seconds": {"type": "number"},
                },
                "required": ["action"],
            },
        },
        "memory": {"type": ["string", "null"]},
    },
    "required": ["goal", "purpose", "objective", "goal_status", "progress", "reason", "actions", "memory"],
}


PICKAXE_LEVEL = {
    "wooden_pickaxe": 1,
    "golden_pickaxe": 1,
    "stone_pickaxe": 2,
    "iron_pickaxe": 3,
    "diamond_pickaxe": 4,
    "netherite_pickaxe": 4,
}


def _normalize_memory(value):
    if isinstance(value, str):
        value = value.strip()
        return value[:300] if value else None

    if isinstance(value, list):
        text = "; ".join(
            str(item).strip()
            for item in value
            if isinstance(
                item,
                (str, int, float),
            )
        )

        return text[:300] or None

    if isinstance(value, dict):
        for key in (
            "fact",
            "discovery",
            "location",
            "note",
            "text",
        ):
            if (
                isinstance(
                    value.get(key),
                    str,
                )
                and value[key].strip()
            ):
                return value[key].strip()[:300]

    return None


def _has_pickaxe(inventory, level=1):
    return any(
        inventory.get(name, 0) > 0
        and tool_level >= level
        for name, tool_level
        in PICKAXE_LEVEL.items()
    )


def _known_resource_targets(state, recent):
    known = []
    for block in state.get("nearby_blocks", []):
        if isinstance(block, dict) and isinstance(block.get("position"), dict):
            known.append({"type": block.get("type"), "position": block["position"], "nearby": True})
    for event in reversed(recent):
        discovered = (event.get("result", {}).get("state_change", {}).get("discovered") if isinstance(event, dict) else None)
        if isinstance(discovered, dict) and isinstance(discovered.get("position"), dict):
            known.append({"type": discovered.get("type"), "position": discovered["position"], "nearby": False})
    unique = []
    seen = set()
    for target in known:
        position = target.get("position", {})
        key = (target.get("type"), position.get("x"), position.get("y"), position.get("z"))
        if target.get("type") and key not in seen:
            seen.add(key)
            unique.append(target)
    return unique[:12]


def _prefer_known_resource_targets(decision, state, recent):
    """Replace redundant exploration with movement/collection toward a known target."""
    if not isinstance(decision, dict) or not isinstance(decision.get("actions"), list):
        return decision
    known = _known_resource_targets(state, recent)
    if not known:
        return decision
    objective = decision.get("objective") if isinstance(decision.get("objective"), dict) else {}
    objective_item = objective.get("item") if objective.get("type") == "obtain_item" else None
    position = state.get("position") or {}
    rewritten = []
    for original in decision["actions"]:
        action = dict(original) if isinstance(original, dict) else original
        if not isinstance(action, dict) or action.get("action") != "explore":
            rewritten.append(action)
            continue
        requested = action.get("targets") if isinstance(action.get("targets"), list) else []
        desired = set(requested or ([objective_item] if objective_item else []))
        target = next((candidate for candidate in known if candidate.get("type") in desired), None)
        if not target:
            rewritten.append(action)
            continue
        target_position = target["position"]
        coordinates = (target_position.get("x"), target_position.get("y"), target_position.get("z"))
        if not all(isinstance(value, (int, float)) for value in coordinates):
            rewritten.append(action)
            continue
        distance_squared = sum((float(position.get(axis, 0)) - float(target_position[axis])) ** 2 for axis in ("x", "y", "z"))
        if target.get("nearby") and distance_squared <= 16:
            rewritten.append({"action": "collect", "block": target["type"], "count": 1})
        else:
            rewritten.append({"action": "move_to", "target": dict(target_position), "range": 3})
    decision = dict(decision)
    decision["actions"] = rewritten
    return decision


def _action_strategy_signature(action):
    if not isinstance(action, dict):
        return None
    return json.dumps(
        {
            key: action.get(key)
            for key in ("action", "block", "item", "entity", "target", "targets")
            if action.get(key) is not None
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _requirement_signature(goals):
    return json.dumps(
        {
            "missing": goals.get("missing_resources", {}),
            "subgoal": goals.get("current_subgoal"),
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _active_replan_requirement(recent, goals):
    current_goal = goals.get("current_goal")
    current_signature = _requirement_signature(goals)
    for event in reversed(recent):
        result = event.get("result", {}) if isinstance(event, dict) else {}
        details = result.get("details", {}) if isinstance(result, dict) else {}
        if (
            details.get("current_goal") == current_goal
            and details.get("requirement_signature") == current_signature
            and isinstance(details.get("rejected_action"), dict)
        ):
            return {
                "reason": result.get("reason"),
                "rejected_action": details["rejected_action"],
                "rejected_plan_actions": details.get("rejected_plan_actions", []),
                "instruction": "Return a different first action and strategy for the unchanged deterministic requirement state.",
            }
    return None


def _repeats_rejected_strategy(decision, replan_required):
    if not replan_required or not isinstance(decision, dict):
        return False
    actions = decision.get("actions")
    if not isinstance(actions, list) or not actions:
        return False
    return _action_strategy_signature(actions[0]) == _action_strategy_signature(replan_required.get("rejected_action"))


def _blocked_action_match(decision, blocked_signatures):
    """
    Check whether the decision's first action matches an action that has
    already failed repeatedly (agent.py's blocked_signatures), independent
    of whether a deterministic requirement guard exists or fired.
    """
    if not blocked_signatures or not isinstance(decision, dict):
        return None
    actions = decision.get("actions")
    if not isinstance(actions, list) or not actions:
        return None
    signature = _action_strategy_signature(actions[0])
    return blocked_signatures.get(signature)


def _replacement_is_required(state, goals):
    understanding = state.get("understanding", {})
    health = state.get("health") if isinstance(state.get("health"), (int, float)) else 20
    food = state.get("food") if isinstance(state.get("food"), (int, float)) else 20
    if (health <= 8 and understanding.get("nearby_danger")) or (food <= 6 and understanding.get("food_items")):
        return True
    impossible = {"requirement_resolution_failed", "invalid_objective_item"}
    return any(blocker.get("reason") in impossible for blocker in goals.get("blockers", []))


def _validate_action(
    action,
    state,
    goal,
):
    name = action.get("action")

    inventory = state.get(
        "inventory",
        {},
    )

    visible = {
        block.get("type")
        for block
        in state.get(
            "nearby_blocks",
            [],
        )
    }

    # ---------------------------------------------------------
    # MINE / COLLECT
    # ---------------------------------------------------------

    if name in {
        "mine",
        "collect",
    }:
        block = action.get(
            "block"
        )

        if (
            not isinstance(
                block,
                str,
            )
            or not block
        ):
            raise ValueError(
                "mine/collect requires block"
            )

        if block not in visible:
            raise ValueError(
                f"target_not_observed: "
                f"{block}; explore for it first"
            )

        if (
            block
            in {
                "dirt",
                "grass_block",
                "sand",
                "gravel",
            }
            and not any(
                word
                in (goal or "").lower()
                for word
                in (
                    block,
                    "build",
                    "shelter",
                    "terrain",
                )
            )
        ):
            raise ValueError(
                f"irrelevant_target: {block}"
            )

        required_level = 0

        if block in {
            "stone",
            "cobblestone",
            "coal_ore",
            "copper_ore",
            "iron_ore",
            "lapis_ore",
        }:
            required_level = (
                1
                if block
                in {
                    "stone",
                    "cobblestone",
                    "coal_ore",
                }
                else 2
            )

        elif block in {
            "gold_ore",
            "diamond_ore",
            "redstone_ore",
            "emerald_ore",
        }:
            required_level = 3

        if (
            required_level
            and not _has_pickaxe(
                inventory,
                required_level,
            )
        ):
            raise ValueError(
                f"requires_pickaxe_level_"
                f"{required_level}: {block}"
            )

    # ---------------------------------------------------------
    # CRAFT
    # ---------------------------------------------------------

    elif name == "craft":
        if not isinstance(
            action.get("item"),
            str,
        ):
            raise ValueError(
                "craft requires item"
            )

    # ---------------------------------------------------------
    # EAT
    # ---------------------------------------------------------

    elif (
        name == "eat"
        and inventory.get(
            action.get("item"),
            0,
        ) < 1
    ):
        raise ValueError(
            f"food_not_in_inventory: "
            f"{action.get('item')}"
        )

    # ---------------------------------------------------------
    # EXPLORE
    # ---------------------------------------------------------

    elif name == "explore":
        action["radius"] = min(
            max(
                int(
                    action.get(
                        "radius",
                        36,
                    )
                ),
                12,
            ),
            96,
        )

        targets = action.get(
            "targets"
        )

        if (
            targets is not None
            and (
                not isinstance(
                    targets,
                    list,
                )
                or not all(
                    isinstance(
                        item,
                        str,
                    )
                    for item in targets
                )
            )
        ):
            raise ValueError(
                "explore targets must "
                "be item-name strings"
            )

    return action


class Planner:
    def __init__(
        self,
        llm,
        system_prompt,
        max_batch=3,
    ):
        self.llm = llm
        self.system_prompt = system_prompt
        self.max_batch = max_batch
        self.last_decision_keys = []

    # =========================================================
    # DECISION
    # =========================================================

    def decide(
        self,
        state,
        goals,
        recent,
        long_term,
        experiences=None,
        experience_guidance=None,
        blocked_signatures=None,
        hierarchy_context=None,
        learned_guidance=None,
        world_context=None,
    ):
        blocked_signatures = blocked_signatures or {}
        relevant_experiences = (
            experiences or []
        )[:6]

        experience_guidance = (
            experience_guidance
            or {
                "successful_experiences": [],
                "failed_experiences": [],
                "corrected_strategies": [],
                "rules": [
                    "Use previous experiences as evidence, not absolute commands.",
                    "Prefer previously successful strategies when the current situation is similar.",
                    "Avoid repeating failed actions under similar conditions.",
                    "Use corrected strategies when they apply.",
                    "Prefer higher-reward successful experiences when context is similar.",
                    "Do not copy an old plan blindly if the current state is different.",
                    "Current state, requirements, blockers, and safety constraints have priority.",
                ],
            }
        )

        replan_required = _active_replan_requirement(recent, goals)

        authoritative_goal = {
            "current_goal": goals.get("current_goal"),
            "purpose": goals.get("goal_purpose"),
            "objective": goals.get("goal_objective"),
            "current_subgoal": goals.get("current_subgoal"),
            "next_requirement": goals.get("next_requirement"),
            "requirements": goals.get("goal_requirements", {}),
            "missing_resources": goals.get("missing_resources", {}),
            "blockers": goals.get("blockers", []),
            "completion_condition": goals.get("completion_condition"),
        }

        context = {
            "authoritative_goal": authoritative_goal,
            "replan_required": replan_required,

            "known_failed_actions": [
                {
                    "action": info.get("action"),
                    "reason": info.get("reason"),
                }
                for info in blocked_signatures.values()
            ],
            # -------------------------------------------------
            # GOAL
            # -------------------------------------------------

            "mode": goals.get(
                "mode",
                "free_play",
            ),

            "current_goal": goals.get(
                "current_goal"
            ),

            "goal_status": goals.get(
                "goal_status",
                (
                    "choose"
                    if not goals.get(
                        "current_goal"
                    )
                    else "continue"
                ),
            ),

            "goal_progress": goals.get(
                "goal_progress"
            ),

            "goal_purpose": goals.get(
                "goal_purpose"
            ),

            "goal_objective": goals.get(
                "goal_objective"
            ),

            # -------------------------------------------------
            # REQUIREMENTS
            # -------------------------------------------------

            "requirements": goals.get(
                "goal_requirements",
                {},
            ),

            "missing_resources": goals.get(
                "missing_resources",
                {},
            ),

            "current_subgoal": goals.get(
                "current_subgoal"
            ),

            "blockers": goals.get(
                "blockers",
                [],
            ),

            "completion_condition": goals.get(
                "completion_condition"
            ),

            # -------------------------------------------------
            # CURRENT STATE
            # -------------------------------------------------

            "state": state,

            "recent": recent[-5:],

            "memory": long_term[-8:],

            # -------------------------------------------------
            # EXPERIENCE
            # -------------------------------------------------

            "relevant_experiences":
                relevant_experiences,

            "known_resource_targets":
                _known_resource_targets(state, recent),

            "experience_guidance":
                experience_guidance,

            # -------------------------------------------------
            # HIERARCHICAL / CONTINUAL LEARNING
            # -------------------------------------------------

            "hierarchy":
                hierarchy_context or {},

            "learned_guidance":
                learned_guidance or {},

            "world_model":
                world_context or {},

            # -------------------------------------------------
            # FREE PLAY
            # -------------------------------------------------

            "free_play_choices": (
                suggest_free_play_goals(
                    state
                )
                if not goals.get(
                    "current_goal"
                )
                else []
            ),

            "max_actions": self.max_batch,
        }

        # -----------------------------------------------------
        # MODEL REQUEST
        # -----------------------------------------------------
        planner_request = (
            "Generate exactly one Minecraft decision matching the required decision schema. "
            "Do not echo, summarize, or return the planner context or experience guidance. "
            "They are input evidence only.\nPLANNER_CONTEXT_JSON:\n"
            + json.dumps(context, separators=(",", ":"))
        )
        try:
            decision = self.llm.complete_json(
                self.system_prompt,
                planner_request,
                response_schema=DECISION_SCHEMA,
            )

        except Exception as exc:
            print(f"[PLANNER] LLM failed: {exc}")

            return {
                "goal": goals.get("current_goal", "survive"),
                "purpose": "Recover from temporary LLM failure",
                "objective": {
                    "type": "survive",
                },
                "goal_status": "continue",
                "progress": "LLM temporarily unavailable",
                "reason": str(exc),
                "actions": [
                    {
                        "action": "wait",
                        "seconds": 1,
                    }
                ],
                "memory": None,
            }

        print("[LLM REPLAN DECISION]", decision)

        print(
            "[LLM DECISION] "
            f"{json.dumps(decision, separators=(',', ':'), ensure_ascii=False)}"
        )

        self.last_decision_keys = (
            sorted(decision.keys())
            if isinstance(decision, dict)
            else [f"<{type(decision).__name__}>"]
        )

        avoid_action = None
        avoid_reason = None

        if _repeats_rejected_strategy(decision, replan_required):
            avoid_action = replan_required["rejected_action"]
            avoid_reason = replan_required.get("reason")
        else:
            blocked_match = _blocked_action_match(decision, blocked_signatures)

            if blocked_match:
                avoid_action = decision["actions"][0]
                avoid_reason = blocked_match.get("reason")

        if avoid_action:
            repair_request = (
                planner_request
                + "\nREPLAN_CORRECTION:\nYour previous answer repeated an action that has "
                f"already failed repeatedly (reason: {avoid_reason}). "
                "Return a genuinely different first action that still advances authoritative_goal. "
                f"Do not repeat: {json.dumps(avoid_action, separators=(',', ':'))}"
            )
            decision = self.llm.complete_json(
                self.system_prompt,
                repair_request,
                response_schema=DECISION_SCHEMA,
            )
            print(
                "[LLM REPLAN DECISION] "
                f"{json.dumps(decision, separators=(',', ':'), ensure_ascii=False)}"
            )
            self.last_decision_keys = (
                sorted(decision.keys())
                if isinstance(decision, dict)
                else [f"<{type(decision).__name__}>"]
            )

            if _repeats_rejected_strategy(decision, replan_required):
                raise ValueError("Model repeated a deterministically rejected plan")

            if _blocked_action_match(decision, blocked_signatures):
                raise ValueError("Model repeated a previously failed action")

        decision = _prefer_known_resource_targets(
            decision,
            state,
            recent,
        )

        return self.validate(
            decision,
            state,
            goals,
        )

    # =========================================================
    # VALIDATION
    # =========================================================

    def validate(
        self,
        decision,
        state=None,
        goals=None,
    ):
        if not isinstance(
            decision,
            dict,
        ):
            raise ValueError(
                "Decision must be an object"
            )

        goal = decision.get(
            "goal"
        )

        purpose = decision.get(
            "purpose"
        )

        objective = decision.get(
            "objective"
        )

        actions = decision.get(
            "actions"
        )

        if (
            not isinstance(
                goal,
                str,
            )
            or not goal.strip()
        ):
            raise ValueError(
                "Decision requires a goal"
            )

        if (
            not isinstance(
                actions,
                list,
            )
            or not 1 <= len(
                actions
            ) <= self.max_batch
        ):
            raise ValueError(
                f"Decision requires "
                f"1-{self.max_batch} actions"
            )

        state = state or {}

        understanding = state.get(
            "understanding",
            {},
        )

        first_name = (
            actions[0].get("action")
            if isinstance(
                actions[0],
                dict,
            )
            else None
        )

        # -----------------------------------------------------
        # HUNGER
        # -----------------------------------------------------

        if (
            (
                state.get("food")
                if isinstance(
                    state.get("food"),
                    (int, float),
                )
                else 20
            )
            <= 6
            and understanding.get(
                "food_items"
            )
            and first_name != "eat"
        ):
            raise ValueError(
                "critical_hunger: "
                "eat available food "
                "before other actions"
            )

        # -----------------------------------------------------
        # DANGER
        # -----------------------------------------------------

        if (
            (
                state.get("health")
                if isinstance(
                    state.get("health"),
                    (int, float),
                )
                else 20
            )
            <= 8
            and understanding.get(
                "nearby_danger"
            )
            and first_name
            not in {
                "attack",
                "move_to",
                "eat",
            }
        ):
            raise ValueError(
                "immediate_danger: "
                "defend, recover, "
                "or move away"
            )

        # -----------------------------------------------------
        # ACTIVE GOAL
        # -----------------------------------------------------

        active_goal = (
            (goals or {}).get(
                "current_goal"
            )
            or goal
        )

        validated_actions = []

        for action in actions:
            if (
                not isinstance(
                    action,
                    dict,
                )
                or action.get(
                    "action"
                )
                not in ALLOWED_ACTIONS
            ):
                raise ValueError(
                    "Decision contains "
                    "an invalid action"
                )

            validated_actions.append(
                _validate_action(
                    dict(action),
                    state,
                    active_goal,
                )
            )

        # -----------------------------------------------------
        # GOAL STATUS
        # -----------------------------------------------------

        # -----------------------------------------------------
        # AUTHORITATIVE ACTIVE GOAL
        # -----------------------------------------------------

        active_goal = (
            (goals or {}).get("current_goal")
            or goal
        )

        active_objective = (
            (goals or {}).get("goal_objective")
        )

        status = decision.get(
            "goal_status",
            "continue",
        )

        if status not in {
            "continue",
            "complete",
            "replace",
        }:
            status = "continue"

        # ---------------------------------------------------------
        # LONG-HORIZON GOAL LOCK
        #
        # When an active deterministic objective exists, the LLM
        # cannot silently replace it with "oak_planks", "stick",
        # "basic tools", etc.
        # ---------------------------------------------------------

        if (
            (goals or {}).get("current_goal")
            and active_objective
        ):
            model_objective = (
                objective
                if isinstance(objective, dict)
                else None
            )

            # Keep the deterministic objective.
            objective = active_objective

            # Keep the original long-term goal.
            goal = active_goal

            # Normal LLM planning is always continuation.
            #
            # Completion is handled by deterministic assessment
            # in AutonomousAgent.run().
            if status == "replace":
                status = "continue"

            if status == "complete":
                status = "continue"

        else:
            # -----------------------------------------------------
            # Normal free-play behavior
            # -----------------------------------------------------

            if (
                (goals or {}).get("current_goal")
                and status == "replace"
                and not _replacement_is_required(
                    state,
                    goals or {},
                )
            ):
                status = "continue"

            if (
                (goals or {}).get("current_goal")
                and goal.strip() != active_goal
                and status == "continue"
            ):
                goal = active_goal

            # -------------------------------------------------
            # REJECT ALREADY-SATISFIED FRESH OBJECTIVES
            #
            # With no active goal, the model is free to propose a
            # new one. If it proposes obtaining an item the bot
            # already has enough of, the deterministic assessment
            # will mark it "complete" on the very next cycle with
            # zero actions taken, and free-play selection tends to
            # just re-propose the same already-done goal again,
            # forever. Reject it up front and make the model pick
            # something that actually requires work.
            # -------------------------------------------------

            if (
                not (goals or {}).get("current_goal")
                and isinstance(objective, dict)
                and objective.get("type") == "obtain_item"
            ):
                item = objective.get("item")

                try:
                    requested_count = max(
                        1, int(objective.get("count", 1))
                    )
                except (TypeError, ValueError):
                    requested_count = 1

                have = (
                    int(
                        (state.get("inventory") or {}).get(item, 0)
                        or 0
                    )
                    if isinstance(item, str)
                    else 0
                )

                if item and have >= requested_count:
                    raise ValueError(
                        f"objective_already_satisfied: already have "
                        f"{have}x {item} (requested {requested_count}); "
                        "choose a higher count or a different goal"
                    )

        # -----------------------------------------------------
        # NORMALIZED DECISION
        # -----------------------------------------------------

        return {
            "goal": goal[:200],

            "purpose": str(
                purpose
                or decision.get("reason")
                or goal
            )[:240],

            "objective": (
                objective
                if isinstance(objective, dict)
                else {
                    "type": "strategy",
                    "completion": str(goal)[:160],
                }
            ),

            "goal_status": status,

            "progress": str(
                decision.get(
                    "progress",
                    "",
                )
            )[:240],
  
            "reason": str(
                decision.get(
                    "reason",
                    "",
                )
            )[:300],

            "actions": validated_actions,

            "memory": _normalize_memory(
                decision.get("memory")
            ),
        }
