import json
import time
from pathlib import Path


class SkillLibrary:
    """Durable reusable action sequences learned from successful episodes."""

    def __init__(self, path, limit=300):
        self.path = Path(path)
        self.limit = max(20, int(limit))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            self.skills = loaded if isinstance(loaded, list) else []
        except (FileNotFoundError, json.JSONDecodeError):
            self.skills = []
        self.skills = [s for s in self.skills if isinstance(s, dict)][-self.limit:]

    @staticmethod
    def _signature(actions):
        return json.dumps(actions, sort_keys=True, separators=(",", ":"))

    def _save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.skills[-self.limit:], indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def upsert(self, *, goal, objective, actions, outcome="success", context=None):
        actions = [dict(a) for a in actions if isinstance(a, dict)]
        if not actions:
            return
        signature = self._signature(actions)
        for skill in self.skills:
            if skill.get("signature") == signature and skill.get("goal") == goal:
                if outcome == "success":
                    skill["success_count"] = int(skill.get("success_count", 0)) + 1
                else:
                    skill["failure_count"] = int(skill.get("failure_count", 0)) + 1
                skill["last_used"] = time.time()
                self._save()
                return
        self.skills.append({
            "id": f"skill-{int(time.time() * 1000)}",
            "name": f"{str(goal)[:80]} sequence",
            "goal": goal,
            "objective": objective,
            "actions": actions[:8],
            "signature": signature,
            "success_count": 1 if outcome == "success" else 0,
            "failure_count": 1 if outcome != "success" else 0,
            "context": context or {},
            "created_at": time.time(),
            "last_used": time.time(),
        })
        self._save()

    def retrieve(self, goal, objective=None, limit=5):
        query = str(goal or "").lower()
        item = str((objective or {}).get("item", "")).lower()
        scored = []
        for skill in self.skills:
            hay = f"{skill.get('goal','')} {skill.get('name','')} {skill.get('objective',{}).get('item','')}".lower()
            score = 0
            if query and query in hay:
                score += 3
            if item and item in hay:
                score += 4
            score += min(3, int(skill.get("success_count", 0))) * 0.5
            score -= min(2, int(skill.get("failure_count", 0))) * 0.25
            if score > 0:
                scored.append((score, skill))
        scored.sort(key=lambda x: (x[0], x[1].get("last_used", 0)), reverse=True)
        return [
            {
                "name": s.get("name"),
                "goal": s.get("goal"),
                "objective": s.get("objective"),
                "actions": s.get("actions", []),
                "success_count": s.get("success_count", 0),
                "failure_count": s.get("failure_count", 0),
                "relevance_score": round(score, 3),
            }
            for score, s in scored[:limit]
        ]
