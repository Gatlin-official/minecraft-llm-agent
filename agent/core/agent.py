import json
import time
import inspect

from .state import compact_state
from .planner import _action_strategy_signature
from ..memory.experience_policy import ExperiencePolicy


class AutonomousAgent:
    def __init__(
        self,
        minecraft,
        planner,
        goals,
        short_memory,
        long_memory,
        config,
        requirement_planner=None,
        experience_store=None,
        experience_evaluator=None,
        semantic_memory=None,
        skill_library=None,
        learner=None,
        world_model=None,
        hierarchy=None,
    ):
        self.minecraft = minecraft
        self.planner = planner
        self.goals = goals
        self.short_memory = short_memory
        self.long_memory = long_memory

        self.requirement_planner = requirement_planner
        self.experience_store = experience_store
        self.experience_evaluator = experience_evaluator
        self.semantic_memory = semantic_memory
        self.skill_library = skill_library
        self.learner = learner
        self.world_model = world_model
        self.hierarchy = hierarchy

        self.experience_policy = (
            ExperiencePolicy(experience_store)
            if experience_store
            else None
        )

        self.interval = max(
            1,
            config["decision_interval_seconds"],
        )

        self.error_limit = max(
            1,
            config["consecutive_error_limit"],
        )

        self.running = True
        self.last_failure = None
        self.repeated_failures = 0

        # Actions whose exact (action, target) signature has failed
        # `error_limit` times in a row. Blocked for a few cycles so the
        # planner is forced to try something else instead of being
        # re-asked the same near-identical prompt and (at temperature 0)
        # producing the same decision again.
        self.blocked_signatures = {}

        # Minecraft /state can briefly lag behind an action's
        # deterministic state_change.
        self._pending_state_transition = None

    # =========================================================
    # STATE CHANGE MERGING
    # =========================================================

    @staticmethod
    def _apply_state_change(state, result):
        """
        Merge deterministic state changes reported by a successful
        Minecraft action into the freshly observed state.

        Action state_change values are deltas, not full snapshots.
        """
        if not isinstance(state, dict):
            state = {}

        if not isinstance(result, dict):
            return state

        change = result.get("state_change")

        if not isinstance(change, dict):
            return state

        merged = dict(state)

        # -----------------------------------------------------
        # INVENTORY DELTAS
        # -----------------------------------------------------

        inventory = dict(
            merged.get("inventory")
            if isinstance(
                merged.get("inventory"),
                dict,
            )
            else {}
        )

        inventory_delta = change.get("inventory")

        if isinstance(inventory_delta, dict):
            for item, delta in inventory_delta.items():
                try:
                    delta = int(delta)
                except (TypeError, ValueError):
                    continue

                current = int(
                    inventory.get(item, 0) or 0
                )

                new_count = current + delta

                if new_count > 0:
                    inventory[item] = new_count
                else:
                    inventory.pop(item, None)

            merged["inventory"] = inventory

        # -----------------------------------------------------
        # BLOCK MINING
        # -----------------------------------------------------

        if "blocks_mined" in change:
            try:
                blocks_mined_delta = int(
                    change.get("blocks_mined", 0) or 0
                )
            except (TypeError, ValueError):
                blocks_mined_delta = 0

            try:
                current_blocks_mined = int(
                    merged.get("blocks_mined", 0) or 0
                )
            except (TypeError, ValueError):
                current_blocks_mined = 0

            merged["blocks_mined"] = (
                current_blocks_mined
                + blocks_mined_delta
            )

        return merged

    def _reconcile_pending_state(self, state):
        """
        Reconcile a possibly stale Minecraft /state snapshot with the
        previous action's deterministic state_change.

        We only apply the pending delta when the affected inventory
        entries are still exactly at their pre-action values.

        This prevents:
        - stale /state responses losing successful inventory updates
        - double-counting when /state already contains the update
        """
        pending = self._pending_state_transition

        if not pending:
            return state

        # Consume the pending transition exactly once.
        self._pending_state_transition = None

        baseline = pending.get("baseline")
        result = pending.get("result")

        if not isinstance(baseline, dict):
            return state

        if not isinstance(result, dict):
            return state

        change = result.get("state_change")

        if not isinstance(change, dict):
            return state

        inventory_delta = change.get("inventory")

        if (
            not isinstance(inventory_delta, dict)
            or not inventory_delta
        ):
            return state

        live_inventory = (
            state.get("inventory")
            if isinstance(
                state.get("inventory"),
                dict,
            )
            else {}
        )

        baseline_inventory = (
            baseline.get("inventory")
            if isinstance(
                baseline.get("inventory"),
                dict,
            )
            else {}
        )

        # Determine whether /state is stale.
        #
        # If all affected inventory values are still exactly equal
        # to the pre-action values, the action result has not yet
        # propagated through /state.
        stale = True

        for item in inventory_delta:
            try:
                live_count = int(
                    live_inventory.get(item, 0) or 0
                )

                baseline_count = int(
                    baseline_inventory.get(item, 0) or 0
                )

            except (TypeError, ValueError):
                stale = False
                break

            if live_count != baseline_count:
                stale = False
                break

        if not stale:
            # /state already reflects the action.
            return state

        print(
            "[STATE] /state appears stale; "
            "applying deterministic action state_change"
        )

        return self._apply_state_change(
            state,
            result,
        )

    # =========================================================
    # MAIN LOOP
    # =========================================================

    def run(self):
        failures = 0

        print(
            f"[AGENT] Started in {self.goals.mode} mode"
        )

        while self.running:
            try:
                # -------------------------------------------------
                # READ CURRENT MINECRAFT STATE
                # -------------------------------------------------

                state = self.minecraft.state()

                if not state.get("connected"):
                    print(
                        "[AGENT] Waiting for Minecraft..."
                    )

                    time.sleep(5)
                    continue

                # -------------------------------------------------
                # EXPIRE STALE BLOCKED ACTION SIGNATURES
                # -------------------------------------------------

                for signature in list(self.blocked_signatures):
                    self.blocked_signatures[signature]["cycles_remaining"] -= 1

                    if self.blocked_signatures[signature]["cycles_remaining"] <= 0:
                        del self.blocked_signatures[signature]

                compact = compact_state(state)

                # Build a lightweight world model before planning.
                world_context = (
                    self.world_model.observe(compact)
                    if self.world_model
                    else {}
                )

                # Reconcile a transiently stale /state response
                # with the previous successful state-changing action.
                compact = self._reconcile_pending_state(
                    compact
                )

                # -------------------------------------------------
                # REQUIREMENT ASSESSMENT
                # -------------------------------------------------

                assessment = self._assess(
                    compact
                )

                # -------------------------------------------------
                # DETERMINISTIC GOAL COMPLETION
                # -------------------------------------------------

                if (
                    assessment
                    and assessment.get("complete")
                ):
                    completed_goal = (
                        self.goals.current
                    )

                    print(
                        f"[GOAL] COMPLETE: {completed_goal}"
                    )

                    completion_plan = {
                        "action": "goal_complete",
                        "recent_strategy": (
                            self.short_memory.summary()[-6:]
                        ),
                    }

                    self._store_experience(
                        compact,
                        completion_plan,
                        {
                            "success": True,
                            "reason": (
                                "completion_condition_met"
                            ),
                        },
                        "Goal completion condition met.",
                        5,
                    )

                    completed_objective = self.goals.objective
                    if self.learner:
                        self.learner.consolidate(
                            completed_goal,
                            completed_objective,
                            completed=True,
                        )
                    self.goals.complete_active()

                    assessment = None

                # -------------------------------------------------
                # EXPERIENCE RETRIEVAL
                # -------------------------------------------------

                experience_query = " ".join(
                    filter(
                        None,
                        [
                            self.goals.current,
                            self.goals.subgoal,
                            str(self.goals.blockers),
                        ],
                    )
                )

                experiences = (
                    self.experience_store.retrieve(
                        experience_query,
                        limit=6,
                    )
                    if self.experience_store
                    else []
                )

                # -------------------------------------------------
                # EXPERIENCE POLICY
                # -------------------------------------------------

                experience_guidance = (
                    self.experience_policy.guidance(
                        experiences
                    )
                    if self.experience_policy
                    else {}
                )

                learned_guidance = (
                    self.learner.guidance(
                        self.goals.current,
                        self.goals.objective,
                    )
                    if self.learner
                    else {}
                )

                hierarchy_context = (
                    self.hierarchy.build(
                        self.goals.context(),
                        assessment,
                        world_context,
                        learned_guidance,
                    )
                    if self.hierarchy
                    else {}
                )

                # -------------------------------------------------
                # ASK PLANNER
                # -------------------------------------------------

                planner_kwargs = {
                    "hierarchy_context": hierarchy_context,
                    "learned_guidance": learned_guidance,
                    "world_context": world_context,
                }
                try:
                    supported = inspect.signature(self.planner.decide).parameters
                    planner_kwargs = {
                        key: value
                        for key, value in planner_kwargs.items()
                        if key in supported
                    }
                except (TypeError, ValueError):
                    planner_kwargs = {}

                decision = self.planner.decide(
                    compact,
                    self.goals.context(),
                    self.short_memory.summary(),
                    self.long_memory.relevant(),
                    experiences,
                    experience_guidance,
                    self.blocked_signatures,
                    **planner_kwargs,
                )

                # -------------------------------------------------
                # PRESERVE CURRENT GOAL
                # -------------------------------------------------

                previous_goal = self.goals.current
                previous_objective = (
                    self.goals.objective
                )

                decision_status = decision[
                    "goal_status"
                ]

                # -------------------------------------------------
                # PREVENT FALSE COMPLETION
                # -------------------------------------------------

                if (
                    decision_status == "complete"
                    and assessment
                    and (
                        self.goals.objective
                        or {}
                    ).get("type")
                    == "obtain_item"
                    and not assessment.get("complete")
                ):
                    decision_status = "continue"

                    print(
                        "[PLAN] Completion rejected: "
                        "deterministic requirements are still missing"
                    )

                # -------------------------------------------------
                # UPDATE GOAL
                # -------------------------------------------------

                self.goals.update(
                    decision["goal"],
                    decision_status,
                    decision["progress"],
                    decision["purpose"],
                    decision["objective"],
                )

                # -------------------------------------------------
                # REASSESS IF OBJECTIVE CHANGED
                # -------------------------------------------------

                if (
                    assessment is None
                    or self.goals.objective
                    != previous_objective
                ):
                    assessment = self._assess(
                        compact
                    )

                if (
                    decision_status == "complete"
                    and previous_goal
                ):
                    print(
                        f"[GOAL] Complete: {previous_goal}"
                    )

                if self.goals.current:
                    print(
                        f"[GOAL] {self.goals.current}"
                    )

                # -------------------------------------------------
                # PRINT REQUIREMENT PROGRESS
                # -------------------------------------------------

                if assessment:
                    progress = ", ".join(
                        f"{name} "
                        f"{values['current']}/"
                        f"{values['required']}"
                        for name, values
                        in assessment.get(
                            "requirements",
                            {},
                        ).items()
                        if values.get(
                            "missing",
                            0,
                        ) > 0
                    )

                    print(
                        "[PROGRESS] "
                        f"{progress or 'completion condition pending'} "
                        f"({assessment.get('progress_percent', 0)}%)"
                    )

                    print(
                        "[SUBGOAL] "
                        f"{assessment.get('subgoal')}"
                    )

                    if assessment.get(
                        "blockers"
                    ):
                        print(
                            "[BLOCKERS] "
                            f"{assessment['blockers']}"
                        )

                # -------------------------------------------------
                # PRINT PLAN
                # -------------------------------------------------

                print(
                    "[PLAN] "
                    f"{decision['reason'] or decision['progress'] or 'Continue current goal'}"
                )

                print(
                    "[PLAN ACTIONS] "
                    f"{json.dumps(decision['actions'], separators=(',', ':'))}"
                )

                # -------------------------------------------------
                # LONG-TERM MEMORY
                # -------------------------------------------------

                if decision.get("memory"):
                    self.long_memory.add(
                        decision["memory"]
                    )

                # -------------------------------------------------
                # EXECUTE ACTIONS
                # -------------------------------------------------

                for action in decision["actions"]:
                    if not self.running:
                        print(
                            "[EXECUTE] skipped: agent stopping"
                        )
                        break

                    print(
                        f"[GUARD] action={action.get('action')}"
                    )

                    # -------------------------------------------------
                    # REQUIREMENT GUARD
                    # -------------------------------------------------

                    if (
                        assessment
                        and self.requirement_planner
                    ):
                        try:
                            action = (
                                self.requirement_planner.guard_action(
                                    action,
                                    assessment,
                                )
                            )

                        except ValueError as exc:
                            requirement_signature = (
                                self.requirement_planner.marker(
                                    assessment
                                )
                                if self.requirement_planner
                                else None
                            )

                            result = {
                                "success": False,
                                "action": action.get(
                                    "action"
                                ),
                                "target": (
                                    action.get("block")
                                    or action.get("item")
                                ),
                                "reason": str(exc),
                                "state_change": {},
                                "details": {
                                    "current_goal": (
                                        self.goals.current
                                    ),
                                    "current_subgoal": (
                                        assessment.get(
                                            "subgoal"
                                        )
                                    ),
                                    "missing_resources": (
                                        assessment.get(
                                            "missing",
                                            {},
                                        )
                                    ),
                                    "completion_condition": (
                                        assessment.get(
                                            "completion_condition"
                                        )
                                    ),
                                    "requirement_signature": (
                                        requirement_signature
                                    ),
                                    "rejected_action": action,
                                    "rejected_plan_actions": (
                                        decision["actions"]
                                    ),
                                },
                            }

                            self.short_memory.add(
                                action,
                                result,
                            )

                            self.goals.record_result(
                                action,
                                result,
                            )

                            reward = (
                                self.experience_evaluator.evaluate(
                                    action,
                                    result,
                                )
                                if self.experience_evaluator
                                else -2
                            )

                            self._store_experience(
                                compact,
                                action,
                                result,
                                decision["reason"],
                                reward,
                            )

                            print(
                                f"[GUARD] rejected={exc}"
                            )

                            print(
                                "[REPLAN] Requirement validation "
                                "rejected the action"
                            )

                            break

                    # -------------------------------------------------
                    # EXECUTE MINECRAFT ACTION
                    # -------------------------------------------------

                    print(
                        f"[EXECUTE] action={action.get('action')}"
                    )

                    planned_target = (
                        action.get("block")
                        or action.get("item")
                        or action.get("entity")
                        or action.get("target")
                        or action.get("targets")
                    )

                    print(
                        "[ACTION] "
                        f"{action.get('action')} "
                        f"target="
                        f"{json.dumps(planned_target, separators=(',', ':'))}"
                    )

                    # Save the state immediately before the action.
                    #
                    # If Minecraft's next /state response is still
                    # identical to this state, we know the action's
                    # deterministic state_change has not propagated yet.
                    action_baseline_state = dict(
                        compact
                    )

                    # Execute the action
                    result = self.minecraft.act(
                        action
                    )

                    # Capture the real Minecraft state immediately after the action
                    # Store deterministic state change so the next
                    # decision cycle can reconcile stale /state data.
                    if (
                        result.get("success")
                        and isinstance(
                            result.get("state_change"),
                            dict,
                        )
                        and result.get("state_change")
                    ):
                        self._pending_state_transition = {
                            "baseline": (
                                action_baseline_state
                            ),
                            "result": result,
                        }

                    self.short_memory.add(
                        action,
                        result,
                    )

                    self.goals.record_result(
                        action,
                        result,
                    )

                    if self.learner:
                        reward_for_learning = (
                            self.experience_evaluator.evaluate(
                                action,
                                result,
                                required=self._action_is_required(action, assessment),
                            )
                            if self.experience_evaluator
                            else (1 if result.get("success") else -1)
                        )
                        self.learner.observe(
                            state_before=compact,
                            goal=self.goals.current,
                            objective=self.goals.objective,
                            action=action,
                            result=result,
                            reward=reward_for_learning,
                        )

                    target = (
                        result.get("target")
                        or action.get("block")
                        or action.get("item")
                        or action.get("entity")
                    )

                    print(
                        "[RESULT] "
                        f"{'SUCCESS' if result.get('success') else 'FAILED'} "
                        f"reason={result.get('reason')}"
                    )

                    if result.get(
                        "state_change"
                    ):
                        print(
                            "[RESULT] "
                            f"change={result['state_change']}"
                        )

                    # -------------------------------------------------
                    # FAILURE / SUCCESS TRACKING
                    # -------------------------------------------------

                    if result.get("success"):
                        failures = 0
                        self.last_failure = None
                        self.repeated_failures = 0

                    else:
                        failures += 1

                        signature = (
                            action.get("action"),
                            str(target),
                            result.get("reason"),
                        )

                        self.repeated_failures = (
                            self.repeated_failures + 1
                            if signature
                            == self.last_failure
                            else 1
                        )

                        self.last_failure = signature

                        if (
                            self.repeated_failures
                            >= self.error_limit
                        ):
                            strategy_signature = (
                                _action_strategy_signature(action)
                            )

                            if strategy_signature:
                                self.blocked_signatures[
                                    strategy_signature
                                ] = {
                                    "action": action,
                                    "reason": result.get("reason"),
                                    # Held for a few decision cycles;
                                    # decremented once per loop iteration.
                                    "cycles_remaining": 3,
                                }

                            print(
                                "[AGENT] Repeated failure recorded; "
                                "blocking this exact action for a few "
                                "cycles so the planner is forced to "
                                "try something different"
                            )

                            self.repeated_failures = 0

                    # -------------------------------------------------
                    # EXPERIENCE LEARNING
                    # -------------------------------------------------

                    if self.experience_evaluator:
                        required = (
                            self._action_is_required(
                                action,
                                assessment,
                            )
                        )

                        reward = (
                            self.experience_evaluator.evaluate(
                                action,
                                result,
                                required=required,
                            )
                        )

                        # Store failures and valuable successes.
                        if (
                            not result.get("success")
                            or reward >= 2
                        ):
                            self._store_experience(
                                compact,
                                action,
                                result,
                                decision["reason"],
                                reward,
                            )

                            if not result.get(
                                "success"
                            ):
                                corrected = (
                                    self.experience_evaluator.corrected_strategy(
                                        action,
                                        result,
                                    )
                                )

                                print(
                                    "[EXPERIENCE] "
                                    f"{corrected}"
                                )

                    # ------------------------------------------------
                    # -------------------------------------------------
                    if not result.get("success"):
                        break
                time.sleep(
                    self.interval
                )

            # ---------------------------------------------------------
            # PLAN ERROR
            # ---------------------------------------------------------

            except ValueError as exc:
                failures += 1

                feedback = {
                    "success": False,
                    "action": "plan",
                    "reason": str(exc),
                    "state_change": {},
                }

                self.short_memory.add(
                    {"action": "plan"},
                    feedback,
                )

                print(
                    f"[PLAN] Rejected: {exc}"
                )

                print(
                    "[PLAN] Model decision keys: "
                    f"{self.planner.last_decision_keys}"
                )

                time.sleep(
                    self.interval
                )

            # ---------------------------------------------------------
            # RUNTIME ERROR
            # ---------------------------------------------------------

            except RuntimeError as exc:
                failures += 1

                print(
                    f"[AGENT] {exc}"
                )

                time.sleep(
                    min(
                        5 * failures,
                        30,
                    )
                )

    # =========================================================
    # STOP
    # =========================================================

    def stop(self):
        self.running = False

    # =========================================================
    # REQUIREMENT ASSESSMENT
    # =========================================================

    def _assess(self, state):
        if (
            not self.requirement_planner
            or not self.goals.current
            or not self.goals.objective
        ):
            return None

        try:
            assessment = (
                self.requirement_planner.assess(
                    self.goals.objective,
                    state,
                )
            )

            self.goals.apply_assessment(
                assessment
            )

            return assessment

        except (
            RuntimeError,
            ValueError,
        ) as exc:
            assessment = {
                "completion_condition": (
                    self.goals.objective
                ),
                "complete": False,
                "requirements": {},
                "missing": {},
                "progress_percent": 0,
                "subgoal": (
                    "resolve deterministic requirement plan"
                ),
                "blockers": [
                    {
                        "reason": (
                            "requirement_resolution_failed"
                        ),
                        "detail": str(exc),
                    }
                ],
                "nodes": [],
                "actionable": None,
            }

            self.goals.apply_assessment(
                assessment
            )

            return assessment

    # =========================================================
    # REQUIRED ACTION DETECTION
    # =========================================================

    @staticmethod
    def _action_is_required(
        action,
        assessment,
    ):
        if not assessment:
            return False

        target = (
            action.get("block")
            or action.get("item")
        )

        return any(
            node.get("missing", 0) > 0
            and (
                node.get("item") == target
                or target
                in node.get(
                    "gather_blocks",
                    [],
                )
            )
            for node in assessment.get(
                "nodes",
                [],
            )
        )

    # =========================================================
    # STORE EXPERIENCE
    # =========================================================

    def _store_experience(
        self,
        state,
        action,
        result,
        reasoning,
        reward,
    ):
        if not self.experience_store:
            return

        corrected = None

        if (
            not result.get("success")
            and self.experience_evaluator
        ):
            corrected = (
                self.experience_evaluator.corrected_strategy(
                    action,
                    result,
                )
            )

        self.experience_store.add(
            state=state,
            goal=self.goals.current,
            reasoning=reasoning,
            plan=action,
            result=result,
            corrected_strategy=corrected,
            reward=reward,
        )