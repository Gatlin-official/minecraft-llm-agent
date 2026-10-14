class ContinualLearner:
    """Converts transitions into semantic facts and reusable skills."""

    def __init__(self, semantic_memory, skill_library, max_episode_actions=8):
        self.semantic = semantic_memory
        self.skills = skill_library
        self.max_episode_actions = max(2, int(max_episode_actions))
        self.episode = []
        self.last_goal = None
        self.last_objective = None

    @staticmethod
    def _target(action):
        return action.get("block") or action.get("item") or action.get("entity")

    def observe(self, *, state_before, goal, objective, action, result, reward):
        if not isinstance(action, dict) or not isinstance(result, dict):
            return
        success = bool(result.get("success"))
        entry = {
            "action": dict(action),
            "success": success,
            "reward": reward,
            "reason": result.get("reason"),
        }
        if goal != self.last_goal or objective != self.last_objective:
            self.episode = []
        self.last_goal = goal
        self.last_objective = dict(objective or {}) if isinstance(objective, dict) else objective
        self.episode.append(entry)
        self.episode = self.episode[-self.max_episode_actions:]

        target = self._target(action)
        if success:
            if target:
                self.semantic.learn(
                    f"{action.get('action')} {target} succeeded",
                    context=f"goal={goal} objective={objective}",
                    confidence=0.62,
                )
        else:
            reason = result.get("reason", "unknown failure")
            self.semantic.learn(
                f"Avoid repeating {action.get('action')} {target or ''} unchanged after failure: {reason}",
                context=f"goal={goal} objective={objective}",
                confidence=0.72,
                source="failure",
            )
            corrected = result.get("details", {}).get("corrected_strategy") if isinstance(result.get("details"), dict) else None
            if corrected:
                self.semantic.learn(str(corrected), context=f"goal={goal}", confidence=0.8, source="failure")

        successful = [x["action"] for x in self.episode if x["success"]]
        if success and len(successful) >= 2:
            self.skills.upsert(
                goal=goal,
                objective=objective,
                actions=successful,
                outcome="success",
                context={"last_reward": reward},
            )

    def consolidate(self, goal, objective, *, completed=False):
        if completed and self.episode:
            successful = [x["action"] for x in self.episode if x["success"]]
            if successful:
                self.skills.upsert(
                    goal=goal,
                    objective=objective,
                    actions=successful,
                    outcome="success",
                    context={"completed": True},
                )
                self.semantic.learn(
                    f"Successful strategy for {goal}: execute verified progress toward {objective.get('item') if isinstance(objective, dict) else objective}",
                    context="goal completed",
                    confidence=0.9,
                    source="consolidation",
                )
        self.episode = []

    def guidance(self, goal, objective, limit=6):
        return {
            "learned_facts": self.semantic.retrieve(
                f"{goal} {objective}", limit=limit
            ),
            "reusable_skills": self.skills.retrieve(
                goal, objective, limit=4
            ),
        }
