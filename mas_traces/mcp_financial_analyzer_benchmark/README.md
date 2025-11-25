# MCP Financial Analyzer Benchmark

This project republishes the `feat/trace-collection` version of Chen Ishi’s financial analyzer with a friendlier setup: standard Python virtual environments, configurable MCP search servers, and JSONL trace files saved under `logs/`. Each run generates both a Markdown report and a metadata sidecar that captures the exact configuration used.

---

## Quick Start

```bash
cd mas_traces/mcp_financial_analyzer_benchmark
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp mcp_agent.secrets.yaml.example mcp_agent.secrets.yaml
# fill in API keys inside the new secrets file

python main.py "Apple"
```

The CLI auto-loads `mcp_agent.config.yaml`/`mcp_agent.secrets.yaml`, seeds any missing `*_API_KEY` environment variables for the current process, runs the workflow, and restores your shell environment afterward.

---

## Requirements

- Python 3.10+
- Node.js (needed for the Google search shim and the filesystem MCP server)
- Global npm packages:
  - `npm install -g g-search-mcp @modelcontextprotocol/server-filesystem playwright`
  - Install Chromium for Playwright once: `PLAYWRIGHT_BROWSERS_PATH=0 npx playwright install chromium`
- Python dependencies:
  - `pip install -r requirements.txt` (installs `mcp-agent`, `mcp-server-fetch`, Tavily/Bing clients, etc.)
- Optional MCP servers:
  - Tavily (hosted): `npx -y mcp-remote ...`
  - Bing: installed via `requirements.txt` and invoked with `python -m mcp_server_bing.server`

Tip: run `./bin/g-search-headless.mjs --once "Apple stock price today"` to verify the Playwright-based Google shim before your first benchmark run.

---

## Secrets and Environment

All API keys live in `mcp_agent.secrets.yaml`. Copy the example file, then add entries like:

```yaml
openai:
  api_key: "sk-..."
tavily:
  api_key: "tvly-..."
bing:
  api_key: "..."
```

At startup the script reads this file and exports `${PROVIDER}_API_KEY` for each section (`tavily` → `TAVILY_API_KEY`, etc.) unless you already set the variable yourself. These temporary exports are removed when the program exits.

### Selecting LLMs and Models

- CLI flag: `python main.py "Apple" --llm-backend openai --llm-model gpt-4o`
- Env vars:  
  `FINANCIAL_ANALYZER_LLM_BACKEND=openai FINANCIAL_ANALYZER_LLM_MODEL=gpt-4o python main.py`

Available aliases: `google`, `gemini`, `openai`, `anthropic`, or a custom `module.path:ClassName`.

### Choosing Search Providers

The workflow can chain multiple MCP search servers. Configure them in `mcp_agent.config.yaml` and select the order via:

```bash
python main.py --search-providers tavily,bing
# or
FINANCIAL_ANALYZER_SEARCH_PROVIDERS=tavily,bing python main.py
```

Any provider listed but missing from the config or lacking an API key is skipped automatically.

### Debugging Env Seeding

Run `python main.py --print-env-only` to list every API key pulled from the secrets file without launching the agents.

---

## Running the Benchmark

```bash
python main.py "Apple"
```

- Reports land in `company_reports/<company>_report_<timestamp>.md`.
- Traces land in `logs/financial_analyzer_traces-<timestamp>.jsonl`.
- Metadata sidecars (same name + `.metadata.json`) record CLI args, LLM/search configuration, and environment overrides.

Key environment knobs:

| Variable | Purpose |
| --- | --- |
| `FINANCIAL_ANALYZER_SANITY_MODE` | `1` (default) for the short run, `0` for the full workflow |
| `BENCHMARK_LLM_REQUESTS_PER_MIN` + `BENCHMARK_LLM_RATE_PERIOD` | Optional rate limits when using Gemini |
| `FINANCIAL_ANALYZER_SEARCH_PROVIDERS` | Comma-separated priority list for search MCP servers |

---

## Trace Utilities

- **Backfill metadata** for older trace files:

  ```bash
  python scripts/backfill_trace_metadata.py --logs-dir logs
  ```

---

## Batch Runs

Use the shared harness to execute multiple iterations with timeouts:

```bash
cd mas_traces
python run_benchmarks.py --benchmark mcp_financial --runs 3 --timeout 900
```

The harness prints the log filenames for each run so you can archive or diff them afterward.

---

## Troubleshooting Notes

- **Search flakiness:** Google Playwright often hits CAPTCHA walls. Prefer Tavily/Bing when possible, or add multiple providers so the agent can fall back.
- **Yahoo Finance rate limits:** Some traces show `429`/`404` from Yahoo when scraping financial metrics. Supply alternate URLs (MacroTrends, Investing.com, etc.) via the agent prompt if you need redundancy.
- **Environment sanity check:** If Tavily/Bing still complain about missing keys, run `python main.py --print-env-only` to verify the script can see the secrets before launching the workflow.

Feel free to adapt the MCP config for additional servers or exporters—everything is pluggable via `mcp_agent.config.yaml` and the secrets file.
