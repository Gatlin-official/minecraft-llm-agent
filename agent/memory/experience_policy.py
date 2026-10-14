from collections import defaultdict


class ExperiencePolicy:
    """
    Converts stored experiences into practical guidance for the planner.

    The policy does NOT execute actions.
    It only evaluates previous experiences and recommends:
        - successful strategies
        - failed strategies to avoid
        - corrected strategies
    """

    def __init__(self, experience_store):
        self.store = experience_store

    # ---------------------------------------------------------
    # ACTION SIGNATURE
    # ---------------------------------------------------------

    @staticmethod
    def action_signature(action):
        if not isinstance(action, dict):
            return None

        return (
            action.get("action"),
            action.get("block")
            or action.get("item")
            or action.get("entity")
            or action.get("target"),
        )

    # ---------------------------------------------------------
    # EXPERIENCE CLASSIFICATION
    # ---------------------------------------------------------

    @classmethod
    def classify(cls, experience):
        """
        Classify one stored experience.

        Returns:
            success
            failure
            neutral
        """

        if not isinstance(experience, dict):
            return "neutral"

        if experience.get("success") is True:
            return "success"

        if experience.get("success") is False:
            return "failure"

        reward = experience.get("reward", 0)

        try:
            reward = float(reward)
        except (TypeError, ValueError):
            reward = 0

        if reward > 0:
            return "success"

        if reward < 0:
            return "failure"

        return "neutral"

    # ---------------------------------------------------------
    # FAILURE DETECTION
    # ---------------------------------------------------------

    @classmethod
    def failed_action_signatures(cls, experiences):
        """
        Return actions that previously failed.
        """

        failures = set()

        for experience in experiences or []:
            if cls.classify(experience) != "failure":
                continue

            action = experience.get("selected_plan")

            signature = cls.action_signature(action)

            if signature:
                failures.add(signature)

        return failures

    # ---------------------------------------------------------
    # SUCCESS DETECTION
    # ---------------------------------------------------------

    @classmethod
    def successful_action_signatures(cls, experiences):
        """
        Return actions that previously succeeded.
        """

        successes = set()

        for experience in experiences or []:
            if cls.classify(experience) != "success":
                continue

            action = experience.get("selected_plan")

            signature = cls.action_signature(action)

            if signature:
                successes.add(signature)

        return successes

    # ---------------------------------------------------------
    # FAILURE AVOIDANCE
    # ---------------------------------------------------------

    def should_avoid(self, action, experiences):
        """
        Determine whether an action should be avoided because
        a similar action previously failed.

        This is intentionally conservative.

        A previous failure is evidence, not absolute truth.
        """

        signature = self.action_signature(action)

        if not signature:
            return False

        failures = self.failed_action_signatures(experiences)

        return signature in failures

    # ---------------------------------------------------------
    # SUCCESS PREFERENCE
    # ---------------------------------------------------------

    def should_prefer(self, action, experiences):
        """
        Determine whether an action has successful historical
        evidence.
        """

        signature = self.action_signature(action)

        if not signature:
            return False

        successes = self.successful_action_signatures(experiences)

        return signature in successes

    # ---------------------------------------------------------
    # CORRECTED STRATEGIES
    # ---------------------------------------------------------

    @staticmethod
    def corrected_strategies(experiences):
        """
        Extract useful corrections from failed experiences.
        """

        strategies = []

        for experience in experiences or []:
            if experience.get("success") is not False:
                continue

            strategy = experience.get("corrected_strategy")

            if not strategy:
                continue

            strategy = str(strategy).strip()

            if strategy and strategy not in strategies:
                strategies.append(strategy)

        return strategies

    # ---------------------------------------------------------
    # ACTION ANALYSIS
    # ---------------------------------------------------------

    def analyze_actions(self, actions, experiences):
        """
        Analyze candidate actions against previous experience.

        Returns a list containing:

            {
                "action": ...,
                "avoid": True/False,
                "prefer": True/False
            }
        """

        analysis = []

        for action in actions or []:
            analysis.append(
                {
                    "action": action,
                    "avoid": self.should_avoid(
                        action,
                        experiences,
                    ),
                    "prefer": self.should_prefer(
                        action,
                        experiences,
                    ),
                }
            )

        return analysis

    # ---------------------------------------------------------
    # BUILD PLANNER GUIDANCE
    # ---------------------------------------------------------

    def guidance(self, experiences, candidate_actions=None):
        """
        Build compact guidance that can be passed to the planner.

        The result is intentionally small so that it does not
        consume unnecessary model context.
        """

        experiences = experiences or []

        successful = []
        failed = []

        for experience in experiences:
            classification = self.classify(experience)

            if classification == "success":
                successful.append(experience)

            elif classification == "failure":
                failed.append(experience)

        corrections = self.corrected_strategies(experiences)

        guidance = {
            "successful_experiences": [],
            "failed_experiences": [],
            "corrected_strategies": corrections[:4],
            "rules": [
                "Prefer previously successful strategies when the current state is similar.",
                "Avoid repeating an action that previously failed under similar conditions.",
                "Use corrected_strategy from relevant failures when applicable.",
                "Do not blindly copy previous actions.",
                "Current state, requirements, blockers, and safety constraints have priority.",
            ],
        }

        for experience in successful[:4]:
            guidance["successful_experiences"].append(
                {
                    "goal": experience.get("goal"),
                    "plan": experience.get("selected_plan"),
                    "reward": experience.get("reward"),
                }
            )

        for experience in failed[:4]:
            guidance["failed_experiences"].append(
                {
                    "goal": experience.get("goal"),
                    "plan": experience.get("selected_plan"),
                    "failure_reason": experience.get(
                        "failure_reason"
                    ),
                    "corrected_strategy": experience.get(
                        "corrected_strategy"
                    ),
                }
            )

        if candidate_actions:
            guidance["candidate_analysis"] = self.analyze_actions(
                candidate_actions,
                experiences,
            )

        return guidance