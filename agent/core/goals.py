class GoalManager:
    GOAL_PRESETS = {
        "wooden pickaxe": {
            "goal": "make a wooden pickaxe",
            "purpose": "Create and equip a wooden pickaxe for basic resource gathering.",
            "objective": {
                "type": "obtain_item",
                "item": "wooden_pickaxe",
                "count": 1,
            },
        },
        "make wooden pickaxe": {
            "goal": "make a wooden pickaxe",
            "purpose": "Create and equip a wooden pickaxe for basic resource gathering.",
            "objective": {
                "type": "obtain_item",
                "item": "wooden_pickaxe",
                "count": 1,
            },
        },
        "make a wooden pickaxe": {
            "goal": "make a wooden pickaxe",
            "purpose": "Create and equip a wooden pickaxe for basic resource gathering.",
            "objective": {
                "type": "obtain_item",
                "item": "wooden_pickaxe",
                "count": 1,
            },
        },
    }

    def __init__(self, mode="free_play"):
        self.mode = mode
        self.current = None
        self.purpose = None
        self.objective = None

        self.requirements = {}
        self.missing = {}
        self.progress_percent = 0
        self.subgoal = None
        self.blockers = []
        self.completion_condition = None
        self.actionable_requirement = None

        self.last_action = None
        self.last_result = None
        self.stall_count = 0
        self.progress_note = None

        self.completed = []

    def set(self, goal):
        if not isinstance(goal, str) or not goal.strip():
            return

        requested = goal.strip()[:200]
        normalized = requested.lower()

        # ---------------------------------------------------------
        # Recognized long-horizon goals
        # ---------------------------------------------------------

        for phrase, preset in self.GOAL_PRESETS.items():
            if phrase in normalized:
                self.current = preset["goal"]
                self.purpose = preset["purpose"]
                self.objective = dict(preset["objective"])
                return

        # ---------------------------------------------------------
        # Generic goal
        # ---------------------------------------------------------

        self.current = requested

    def update(
        self,
        proposed,
        status="continue",
        progress=None,
        purpose=None,
        objective=None,
    ):
        status = (
            status
            if status in {"continue", "complete", "replace"}
            else "continue"
        )

        # ---------------------------------------------------------
        # No active goal
        # ---------------------------------------------------------

        if self.current is None:
            self.set(proposed)

            if self.current is None:
                return None

            # set() may already have installed a deterministic objective.
            if self.objective is None:
                self.purpose = purpose
                self.objective = objective

        # ---------------------------------------------------------
        # Complete
        # ---------------------------------------------------------

        elif status == "complete":
            self.completed.append(self.current)
            self.completed = self.completed[-5:]

            self.current = None
            self.purpose = None
            self.objective = None

            self.requirements = {}
            self.missing = {}
            self.progress_percent = 0
            self.subgoal = None
            self.blockers = []
            self.completion_condition = None
            self.actionable_requirement = None
            self.stall_count = 0

        # ---------------------------------------------------------
        # Replace
        #
        # IMPORTANT:
        # The planner is allowed to replace a goal only when the
        # caller has explicitly decided that replacement is valid.
        # The Planner now prevents normal LLM responses from reaching
        # this branch for an active long-term goal.
        # ---------------------------------------------------------

        elif status == "replace":
            self.set(proposed)

            if self.current is None:
                return None

            # Preserve deterministic preset objective if one exists.
            if self.objective is None:
                self.purpose = purpose
                self.objective = objective

            self.requirements = {}
            self.missing = {}
            self.progress_percent = 0
            self.subgoal = None
            self.blockers = []
            self.completion_condition = None
            self.actionable_requirement = None
            self.stall_count = 0

        # ---------------------------------------------------------
        # Existing goal
        # ---------------------------------------------------------

        elif self.objective is None and objective is not None:
            self.objective = objective

        if purpose and (
            self.purpose is None
            or status == "replace"
        ):
            self.purpose = str(purpose)[:240]

        if progress:
            self.progress_note = str(progress)[:240]

        return self.current

    def apply_assessment(self, assessment):
        previous = (
            self.progress_percent,
            self.subgoal,
            self.missing,
        )

        self.requirements = assessment.get(
            "requirements",
            {},
        )

        self.missing = assessment.get(
            "missing",
            {},
        )

        self.progress_percent = assessment.get(
            "progress_percent",
            0,
        )

        self.subgoal = assessment.get(
            "subgoal"
        )

        self.blockers = assessment.get(
            "blockers",
            [],
        )

        self.completion_condition = assessment.get(
            "completion_condition"
        )

        self.actionable_requirement = assessment.get(
            "actionable"
        )

        current = (
            self.progress_percent,
            self.subgoal,
            self.missing,
        )

        self.stall_count = (
            self.stall_count + 1
            if current == previous
            else 0
        )

    def record_result(self, action, result):
        self.last_action = action
        self.last_result = result

    def complete_active(self):
        if self.current:
            self.completed.append(self.current)
            self.completed = self.completed[-5:]

        self.current = None
        self.purpose = None
        self.objective = None

        self.requirements = {}
        self.missing = {}
        self.progress_percent = 0
        self.subgoal = None
        self.blockers = []
        self.completion_condition = None
        self.actionable_requirement = None
        self.stall_count = 0

    def context(self):
        return {
            "mode": self.mode,

            "current_goal": self.current,

            "goal_status": (
                "continue"
                if self.current
                else "choose"
            ),

            "goal_purpose": self.purpose,

            "goal_objective": self.objective,

            "goal_requirements": self.requirements,

            "missing_resources": self.missing,

            "goal_progress": {
                "percent": self.progress_percent,
                "note": self.progress_note,
                "last_action": self.last_action,
                "last_result": self.last_result,
                "stall_count": self.stall_count,
            } if self.current else None,

            "current_subgoal": self.subgoal,

            "blockers": self.blockers,

            "completion_condition": self.completion_condition,

            "next_requirement": self.actionable_requirement,

            "recently_completed": self.completed[-3:],
        }