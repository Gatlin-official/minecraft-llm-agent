import json
import math
import re
import time
from pathlib import Path


class SemanticMemory:
    """Durable facts learned from repeated Minecraft experience."""

    def __init__(self, path, limit=1000):
        self.path = Path(path)
        self.limit = max(50, int(limit))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            self.records = loaded if isinstance(loaded, list) else []
        except (FileNotFoundError, json.JSONDecodeError):
            self.records = []
        self.records = [r for r in self.records if isinstance(r, dict)][-self.limit:]

    @staticmethod
    def _tokens(value):
        text = str(value or "").lower().replace("_", " ")
        return {x for x in re.findall(r"[a-z0-9]+", text) if len(x) > 2}

    def _save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.records[-self.limit:], indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def learn(self, fact, *, context="", confidence=0.6, source="experience"):
        fact = str(fact or "").strip()[:500]
        if not fact:
            return
        tokens = self._tokens(fact + " " + context)
        now = time.time()
        for record in self.records:
            if record.get("fact", "").lower() == fact.lower():
                record["confidence"] = round(min(1.0, max(0.0, float(record.get("confidence", 0.5)) + 0.08),), 4)
                record["uses"] = int(record.get("uses", 0)) + 1
                record["updated_at"] = now
                self._save()
                return
        self.records.append({
            "id": f"fact-{int(now * 1000)}",
            "fact": fact,
            "context": str(context)[:400],
            "confidence": round(max(0.0, min(1.0, float(confidence))), 4),
            "source": source,
            "uses": 1,
            "created_at": now,
            "updated_at": now,
            "tokens": sorted(tokens),
        })
        self._save()

    def retrieve(self, query, limit=8):
        q = self._tokens(query)
        if not q:
            return []
        scored = []
        for record in self.records:
            tokens = set(record.get("tokens") or self._tokens(record.get("fact")))
            overlap = len(q & tokens)
            if not overlap:
                continue
            similarity = overlap / max(1, len(q | tokens))
            confidence = float(record.get("confidence", 0.5))
            recency = math.exp(-max(0.0, time.time() - float(record.get("updated_at", 0))) / (30 * 86400))
            score = similarity * 0.75 + confidence * 0.15 + recency * 0.10
            scored.append((score, record))
        scored.sort(key=lambda x: (x[0], x[1].get("updated_at", 0)), reverse=True)
        return [
            {
                "fact": r["fact"],
                "confidence": r.get("confidence", 0.5),
                "source": r.get("source", "experience"),
                "relevance_score": round(s, 4),
            }
            for s, r in scored[:limit]
        ]

    def all_recent(self, limit=20):
        return [{"fact": r.get("fact"), "confidence": r.get("confidence", 0.5)}
                for r in self.records[-limit:]]
