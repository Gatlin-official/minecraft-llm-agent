import json
from pathlib import Path


class LongTermMemory:
    def __init__(self, path, limit=100):
        self.path = Path(path)
        self.limit = limit
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.entries = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            self.entries = []
        if not isinstance(self.entries, list):
            self.entries = []
        self.entries = [entry[:300] for entry in self.entries if isinstance(entry, str) and entry.strip()][-self.limit :]

    def add(self, entry):
        if not isinstance(entry, str) or not entry.strip():
            return
        entry = entry.strip()[:300]
        if entry.lower().startswith(("repeated failure:", "action ", "crafted ", "mined ")):
            return
        if entry in self.entries:
            return
        self.entries.append(entry)
        self.entries = self.entries[-self.limit :]
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.entries, indent=2), encoding="utf-8")
        temporary.replace(self.path)

    def relevant(self, limit=12):
        durable = [entry for entry in self.entries if not entry.lower().startswith("repeated failure:")]
        return durable[-limit:]
