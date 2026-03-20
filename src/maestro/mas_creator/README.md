# mas_creator

`mas_creator` is a lightweight builder for creating multi-agent systems on top of `any-agent`.

It provides:
- Structured agent/group schemas (`AgentInput`, `GroupInput`)
- Builders from Python dicts or JSON files (`AgentBuilder`, `GroupBuilder`)
- Three coordination topologies:
  - `round_robin`
  - `star`
  - `handoff`

## Requirements

- Python 3.10+
- Install `any-agent` and choose a model provider configured for your selected framework. [Click](https://mozilla-ai.github.io/any-agent/) to get more information about any-agent.
- API keys in environment variables (for example `OPENAI_API_KEY`) when needed

## Quick Start (CLI)

Run a group from a JSON config (single turn):

```bash
python src/maestro/mas_creator/main.py path/to/config.json --task "Start conversation"
```

If your config references tool names (strings), provide a tools file:

```bash
python src/maestro/mas_creator/main.py path/to/config.json \
  --tools path/to/tools.py \
  --task "Analyze this request"
```

`tools.py` public functions are auto-loaded and mapped by function name.

Run in interactive multi-turn mode:

```bash
python src/maestro/mas_creator/main.py path/to/config.json \
  --tools path/to/tools.py \
  --interactive
```

In interactive mode, type `exit` / `quit` / `q` to end the session.

## Config Shape

Top-level fields for groups:

- `group_type`: `round_robin` | `star` | `handoff`
- `termination_keyword`: stop signal (default: `TERMINATE`)
- `handoff_prefix`: handoff keyword (only for `handoff` group type)
- Topology-specific fields:
  - `round_robin`: `agents`
  - `star`: `orchestrator`, `sub_agents`
  - `handoff`: `agents`, `entry_agent_name`, optional `handoff_prefix`, `max_turns`, `verbose`

Agent fields:

- `name`
- `framework` (supported by `any-agent`, e.g. `openai`, `google`, `langchain`, `llama_index`, `agno`, `smolagents`, `tinyagent`)
- `model_id` (provider-prefixed, e.g. `openai:gpt-4o-mini`)
- `instructions` / `description`
- optional `tools`, `api_key`, `api_base`, `model_args`, `agent_args`, `human_input`

`human_input` behavior:
- If `human_input: true`, `mas_creator` auto-registers a single-argument tool `human_input(query: str) -> str` for that agent.
- Internally, this wrapper calls `any_agent.tools.send_console_message(user="User", query=query)`.

## Minimal Examples

### 1) Round Robin

```json
{
  "group_type": "round_robin",
  "termination_keyword": "TERMINATE",
  "agents": [
    {
      "name": "planner",
      "framework": "openai",
      "model_id": "openai:gpt-4o-mini",
      "instructions": "Propose a plan and pass context."
    },
    {
      "name": "reviewer",
      "framework": "openai",
      "model_id": "openai:gpt-4o-mini",
      "instructions": "Critique and improve the plan; output TERMINATE when done."
    }
  ]
}
```

### 2) Star

```json
{
  "group_type": "star",
  "orchestrator": {
    "name": "root",
    "framework": "openai",
    "model_id": "openai:gpt-4o-mini",
    "instructions": "Call sub agents as tools and return the final answer."
  },
  "sub_agents": [
    {
      "name": "researcher",
      "framework": "openai",
      "model_id": "openai:gpt-4o-mini",
      "instructions": "Find relevant facts."
    },
    {
      "name": "writer",
      "framework": "openai",
      "model_id": "openai:gpt-4o-mini",
      "instructions": "Write a clear final response."
    }
  ]
}
```

### 3) Handoff

```json
{
  "group_type": "handoff",
  "entry_agent_name": "triage",
  "handoff_prefix": "HANDOFF:",
  "termination_keyword": "TERMINATE",
  "max_turns": 20,
  "agents": [
    {
      "name": "triage",
      "framework": "openai",
      "model_id": "openai:gpt-4o-mini",
      "instructions": "Classify request then output HANDOFF:specialist or TERMINATE."
    },
    {
      "name": "specialist",
      "framework": "openai",
      "model_id": "openai:gpt-4o-mini",
      "instructions": "Solve the issue, then HANDOFF:triage or TERMINATE."
    }
  ]
}
```

## Python API Usage

```python
import asyncio
from maestro.mas_creator import GroupBuilder

async def run():
    group = await GroupBuilder.build_from_config_async(
        "src/maestro/mas_creator/demo/handoff/peer_autonomy_handoff_config.json"
    )
    result = await group.run("Design a practical 4-week community launch plan.")
    print(result)

asyncio.run(run())
```

You can also build directly from Python data:

```python
group = await GroupBuilder.build_from_dict_async(config_dict, tool_registry=my_tools)
```

## Demo Configs

Available demo configs in this repo:
- `src/maestro/mas_creator/demo/travel_planning/travel_config.json`
- `src/maestro/mas_creator/demo/brand_search/brand_config.json`
- `src/maestro/mas_creator/demo/handoff/peer_autonomy_handoff_config.json`

## Notes

- `handoff` is turn-based routing, not true parallel execution.
- In `handoff`, agents should be prompted to always emit either `HANDOFF:<agent_name>` or `TERMINATE`.
- If an unknown handoff target is emitted, execution raises an error.
- `star` now keeps in-memory conversation history across successive `group.run(...)` calls on the same instance.
- Use `group.reset()` to clear stored context before starting a fresh session.
