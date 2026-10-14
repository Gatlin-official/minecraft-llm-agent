import json
import math
import time
from pathlib import Path

from pathlib import Path


class ExperienceStore:
    def __init__(self, path, limit=500):
        self.path = Path(path)
        self.limit = limit
        self.path.parent.mkdir(parents=True, exist_ok=True)

        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            self.records = loaded if isinstance(loaded, list) else []
        except (FileNotFoundError, json.JSONDecodeError):
            self.records = []

    # ---------------------------------------------------------
    # FEATURE EXTRACTION
    # ---------------------------------------------------------

    @staticmethod
    def _tokens(value):
        if value is None:
            return set()

        if isinstance(value, dict):
            value = " ".join(
                f"{key} {item}"
                for key, item in value.items()
            )

        elif isinstance(value, (list, tuple, set)):
            value = " ".join(str(item) for item in value)

        else:
            value = str(value)

        value = (
            value.lower()
            .replace("_", " ")
            .replace("-", " ")
            .replace(":", " ")
            .replace(",", " ")
        )

        return {
            word
            for word in value.split()
            if len(word) > 2
        }

    @classmethod
    def _record_features(cls, record):
        features = set()

        features |= cls._tokens(record.get("goal"))
        features |= cls._tokens(record.get("reasoning_summary"))
        features |= cls._tokens(record.get("failure_reason"))
        features |= cls._tokens(record.get("corrected_strategy"))

        plan = record.get("selected_plan", {})
        if isinstance(plan, dict):
            features |= cls._tokens(plan.get("action"))
            features |= cls._tokens(plan.get("block"))
            features |= cls._tokens(plan.get("item"))
            features |= cls._tokens(plan.get("entity"))
            features |= cls._tokens(plan.get("target"))

        result = record.get("result", {})
        if isinstance(result, dict):
            features |= cls._tokens(result.get("reason"))
            features |= cls._tokens(result.get("target"))

        return features

    # ---------------------------------------------------------
    # STORAGE
    # ---------------------------------------------------------

    def add(
        self,
        *,
        state,
        goal,
        reasoning,
        plan,
        result,
        corrected_strategy=None,
        reward=0,
    ):
        record = {
            "timestamp": int(time.time()),

            "state": {
                "health": state.get("health"),
                "food": state.get("food"),
                "inventory": state.get("inventory", {}),
                "tools": state.get("tools", []),
            },

            "goal": goal,
            "reasoning_summary": str(reasoning)[:240],

            "selected_plan": plan,

            "result": result,
            "success": bool(result.get("success")),

            "failure_reason": (
                None
                if result.get("success")
                else result.get("reason")
            ),

            "corrected_strategy": corrected_strategy,
            "reward": reward,
        }

        self.records.append(record)

        # Keep newest experiences.
        self.records = self.records[-self.limit:]

        temporary = self.path.with_suffix(".tmp")

        temporary.write_text(
            json.dumps(
                self.records,
                indent=2,
            ),
            encoding="utf-8",
        )

        temporary.replace(self.path)

        return record

    # ---------------------------------------------------------
    # RELEVANCE SCORING
    # ---------------------------------------------------------

    @staticmethod
    def _overlap(query_tokens, record_tokens):
        if not query_tokens or not record_tokens:
            return 0.0

        intersection = query_tokens & record_tokens

        if not intersection:
            return 0.0

        # Jaccard similarity.
        union = query_tokens | record_tokens

        return len(intersection) / len(union)

    @staticmethod
    def _reward_score(record):
        reward = record.get("reward", 0)

        try:
            reward = float(reward)
        except (TypeError, ValueError):
            reward = 0

        # Convert reward into a bounded score.
        return math.tanh(reward / 3.0)

    @staticmethod
    def _success_score(record):
        return 1.0 if record.get("success") else 0.0

    @staticmethod
    def _recency_score(record):
        timestamp = record.get("timestamp", 0)

        try:
            age = max(0, time.time() - float(timestamp))
        except (TypeError, ValueError):
            return 0.0

        # Half-life of roughly one week.
        return math.exp(-age / (7 * 24 * 60 * 60))

    @classmethod
    def _score(cls, query_tokens, record):
        record_tokens = cls._record_features(record)

        similarity = cls._overlap(
            query_tokens,
            record_tokens,
        )

        # Completely irrelevant experiences should not receive
        # points merely because they were successful or recent.
        if similarity <= 0:
            return 0.0

        reward = cls._reward_score(record)
        success = cls._success_score(record)
        recency = cls._recency_score(record)

        # Context similarity is the dominant signal.
        # Reward, success and recency only refine an already
        # relevant experience.
        score = (
            similarity * 0.75
            + reward * 0.10
            + success * 0.10
            + recency * 0.05
        )

        return score

    # ---------------------------------------------------------
    # RETRIEVAL
    # ---------------------------------------------------------

    def retrieve(self, query, limit=4):
        query_tokens = self._tokens(query)

        if not query_tokens:
            return []

        scored = []

        for record in self.records:
            score = self._score(
                query_tokens,
                record,
            )

            if score <= 0:
                continue

            scored.append(
                (
                    score,
                    record.get("timestamp", 0),
                    record,
                )
            )

        scored.sort(
            key=lambda entry: (
                entry[0],
                entry[1],
            ),
            reverse=True,
        )

        results = []

        for score, _, record in scored[:limit]:
            results.append(
                {
                    "goal": record.get("goal"),
                    "reasoning_summary": record.get(
                        "reasoning_summary"
                    ),
                    "selected_plan": record.get(
                        "selected_plan"
                    ),
                    "success": record.get(
                        "success"
                    ),
                    "failure_reason": record.get(
                        "failure_reason"
                    ),
                    "corrected_strategy": record.get(
                        "corrected_strategy"
                    ),
                    "reward": record.get(
                        "reward"
                    ),
                    "relevance_score": round(
                        score,
                        4,
                    ),
                }
            )

        return results


class ExperienceEvaluator:

    def __init__(self):
        self.failure_counts = {}

    # ---------------------------------------------------------
    # REWARD
    # ---------------------------------------------------------

    def evaluate(
        self,
        action,
        result,
        *,
        required=False,
        goal_completed=False,
    ):

        signature = (
            action.get("action"),
            action.get("block")
            or action.get("item")
            or action.get("entity"),
        )

        # -------------------------
        # SUCCESS
        # -------------------------

        if result.get("success"):

            self.failure_counts.pop(
                signature,
                None
            )

            if goal_completed:
                return 5

            if action.get("action") == "craft":
                return 2

            if required:
                return 1

            return 0

        # -------------------------
        # FAILURE
        # -------------------------

        count = (
            self.failure_counts.get(
                signature,
                0
            ) + 1
        )

        self.failure_counts[
            signature
        ] = count

        # First failure = -2
        # Second = -3
        # Third = -4
        # etc.
        return -2 - (count - 1)

    # ---------------------------------------------------------
    # CORRECTED STRATEGY
    # ---------------------------------------------------------

    @staticmethod
    def corrected_strategy(
        action,
        result,
    ):

        reason = result.get(
            "reason",
            "unknown failure"
        )

        if (
            reason == "requires_tool"
            or str(reason).startswith(
                "requires_pickaxe"
            )
        ):
            return (
                "Obtain a suitable tool before "
                f"trying to {action.get('action')} "
                f"{action.get('block')}."
            )

        if reason == "requires_crafting_table":
            return (
                "Obtain and place a crafting "
                "table before retrying this recipe."
            )

        if reason == "missing_ingredients":
            return (
                "Obtain the reported missing "
                "ingredients before crafting again."
            )

        if reason == "plan_stalled":
            return (
                "Do not repeat the same action. "
                "Change location, target, or strategy "
                "and verify progress before retrying."
            )

        return (
            "Do not repeat this action unchanged; "
            f"replan around: {reason}."
        )