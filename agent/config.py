import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_config():
    path = Path(os.getenv("CONFIG_PATH", ROOT / "config" / "config.json"))
    if not path.exists():
        path = ROOT / "config" / "config.example.json"
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    data["llm"]["base_url"] = os.getenv("LLM_BASE_URL", data["llm"]["base_url"]).rstrip("/")
    data["llm"]["model"] = os.getenv("LLM_MODEL", data["llm"]["model"])
    data["llm"]["api_key"] = os.getenv("LLM_API_KEY", data["llm"].get("api_key", "local"))
    data["bot_api_url"] = os.getenv("BOT_API_URL", data["bot_api_url"]).rstrip("/")
    data["agent_mode"] = os.getenv("AGENT_MODE", data["agent_mode"])
    return data
