import argparse
from pathlib import Path

from agent.config import ROOT, load_config
from agent.core.agent import AutonomousAgent
from agent.core.goals import GoalManager
from agent.core.planner import Planner
from agent.core.requirements import RequirementPlanner
from agent.llm.client import LLMClient
from agent.memory.long_term import LongTermMemory
from agent.memory.short_term import ShortTermMemory
from agent.memory.semantic import SemanticMemory
from agent.memory.skills import SkillLibrary
from agent.memory.learner import ContinualLearner
from agent.core.world_model import WorldModel
from agent.core.hierarchy import HierarchicalPlanner
from agent.memory.experience import ExperienceEvaluator, ExperienceStore
from agent.tools.minecraft import MinecraftClient


def main():
    parser = argparse.ArgumentParser(description="Local autonomous Minecraft agent")
    parser.add_argument("--goal", help="Initial goal; omit for free-play")
    args = parser.parse_args()
    config = load_config()
    system_prompt = (Path(__file__).parent / "prompts" / "system.txt").read_text(encoding="utf-8")
    llm_config = config["llm"]
    llm = LLMClient(llm_config["base_url"], llm_config["model"], llm_config["api_key"])
    minecraft = MinecraftClient(config["bot_api_url"], config["action_timeout_seconds"] + 5)
    goals = GoalManager("goal" if args.goal else config["agent_mode"])
    goals.set(args.goal)
    semantic_memory = SemanticMemory(
        ROOT / "data" / "memory" / "semantic.json",
        config["memory"].get("semantic_limit", 1000),
    )
    skill_library = SkillLibrary(
        ROOT / "data" / "memory" / "skills.json",
        config["memory"].get("skill_limit", 300),
    )
    agent = AutonomousAgent(
        minecraft,
        Planner(llm, system_prompt, config["maximum_action_batch"]),
        goals,
        ShortTermMemory(config["memory"]["short_term_limit"]),
        LongTermMemory(ROOT / config["memory"]["long_term_file"], config["memory"]["long_term_limit"]),
        config,
        RequirementPlanner(minecraft, config["consecutive_error_limit"]),
        ExperienceStore(ROOT / "data" / "memory" / "experiences.json"),
        ExperienceEvaluator(),
        semantic_memory=semantic_memory,
        skill_library=skill_library,
        learner=ContinualLearner(
            semantic_memory,
            skill_library,
            config["learning"].get("max_episode_actions", 8),
        ),
        world_model=WorldModel(),
        hierarchy=HierarchicalPlanner(),
    )
    try:
        agent.run()
    except KeyboardInterrupt:
        print("\n[AGENT] Stopped")
        agent.stop()


if __name__ == "__main__":
    main()
