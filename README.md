# Minecraft Autonomous AI — Continual Learning Agent

A local Minecraft Java agent with a Mineflayer execution service and a Python hierarchical planning/continual-learning loop. The LLM selects validated actions; it never controls the keyboard or executes generated code.

The architecture combines:
- **Hierarchical planning:** stable long-horizon goals, deterministic prerequisite subgoals, and concrete actions.
- **JARVIS-style memory:** episodic experiences plus durable semantic facts and reusable skills.
- **OpenHA-style separation:** high-level strategy, subgoal planning, and action execution are separate layers.
- **Continual learning:** successful and failed transitions are consolidated into future planning guidance without requiring model fine-tuning.


## Requirements

- Windows 10/11, Node.js 18+, Python 3.10+
- Minecraft Java server or LAN world reachable by Mineflayer
- OpenAI-compatible local LLM server; an 8 GB GPU is suited to a 7–8B 4-bit model

## Installation

```powershell
npm install
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item config\config.example.json config\config.json
```

## Minecraft setup

Start a Java server or open a world to LAN. For an offline/local server, keep `auth` set to `offline`. For an authenticated server, set it to `microsoft`; the first login may request device authorization. Match `minecraft.version` to the server only if automatic detection fails.

## Node.js setup

Edit `config/config.json` or set `MC_HOST`, `MC_PORT`, `MC_USERNAME`, `MC_AUTH`, `MC_VERSION`, and `BOT_PORT`. Start the bot and local API:

```powershell
.\scripts\start_bot.bat
```

The API binds to localhost and exposes `GET /health`, `GET /state`, `POST /action`, and `POST /stop`.

## Python and local LLM setup

Start an OpenAI-compatible local server. The default expects Ollama-compatible chat completions at `http://127.0.0.1:11434/v1`. Set `LLM_BASE_URL`, `LLM_MODEL`, and `LLM_API_KEY`, or edit the `llm` section in `config/config.json`.

```powershell
$env:LLM_MODEL = "your-local-model"
.\scripts\start_agent.bat
```

No third-party Python packages are required.

## Configuration

Runtime settings live in the ignored `config/config.json`. `maximum_action_batch`, `decision_interval_seconds`, memory limits, action timeout, and consecutive error limit control cost and safety.

## Starting the AI

Start Minecraft, then the Node bot, then the Python agent. To give a fixed initial objective:

```powershell
.\scripts\start_agent.bat --goal "Gather wood and build a small shelter"
```

Press `Ctrl+C` to stop the agent. Use `POST /stop` for an emergency bot stop.

## Continual learning

The agent now maintains three durable learning stores under `data/memory/`:

```text
experiences.json  -> raw episodic transitions
semantic.json     -> consolidated facts and lessons
skills.json       -> reusable successful action sequences
```

The learning loop is:

```text
Minecraft state
    -> action
    -> outcome/reward
    -> experience
    -> semantic lesson / skill
    -> retrieval on the next relevant decision
    -> better action
```

This is intentionally model-agnostic. You can later use the accumulated experience records as a training dataset for QLoRA or policy learning.

## Free-play mode

Leave `agent_mode` as `free_play` and start without `--goal`. The planner retains goals across decisions, batches at most the configured number of high-level actions, and re-plans after repeated failures.

## Troubleshooting

- `Minecraft bot is not ready`: wait for spawn and check host, port, version, and server authentication.
- `LLM unavailable`: verify the base URL includes `/v1`, the model is loaded, and the local server is listening.
- Invalid JSON: use an instruction-tuned model with JSON output support or reduce model temperature.
- Pathfinder failures: shorten travel distances and ensure chunks are loaded.

## Architecture

```text
                         Minecraft
                             |
                         Mineflayer
                             |
                     state / action API
                             |
                     +-------v-------+
                     |   World Model |
                     +-------+-------+
                             |
               +-------------v-------------+
               | Hierarchical Agent        |
               | goal -> subgoal -> action |
               +-------------+-------------+
                             |
          +------------------+------------------+
          |                                     |
   Semantic Memory                         Skill Library
   learned facts                           learned sequences
          |                                     |
          +------------------+------------------+
                             |
                      Local LLM / Qwen
                             |
                       validated action
                             |
                          Minecraft

Persistent learning:
experience -> evaluator -> semantic lesson / skill -> retrieval -> next plan
```

The Node layer remains deterministic: state collection, validation, pathfinding, inventory operations, and action execution. The Python layer owns hierarchy, planning, experience retrieval, world modeling, semantic memory, skill learning, and goal continuity.

