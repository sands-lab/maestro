# MCP Financial Analyzer Benchmark

This standalone benchmark packages the `feat/trace-collection` version of Chen Ishi's [MCP financial analyzer](https://github.com/chenIshi/mcp-agent/tree/feat/trace-collection/examples/usecases/mcp_financial_analyzer). It keeps the original OpenTelemetry instrumentation (per-run JSONL files in `logs/`) but swaps the setup instructions to use standard Python virtual environments instead of `uv`.

## 1. Prerequisites

1. System dependencies  
   - Python 3.10+  
   - Node.js for MCP servers (`g-search-mcp`, `mcp-server-filesystem`)  
   - `npm install -g g-search-mcp @modelcontextprotocol/server-filesystem playwright`  
   - Install the Playwright browser bundle once so the g-search MCP server can launch Chromium reliably:

     ```bash
     PLAYWRIGHT_BROWSERS_PATH=0 npx playwright install chromium
     ```

   - Install the fetch MCP server (Python): `pip install mcp-server-fetch`
   - Sanity-check the headless Google search shim before running the benchmark:

     ```bash
     cd mas_traces/mcp_financial_analyzer_benchmark
     ./bin/g-search-headless.mjs --once "Apple stock price today"
     ```

     If this command fails with `ERR_MODULE_NOT_FOUND: playwright`, re-run the global `npm install -g playwright` command above and repeat the browser install.
   - Optional alternative search MCP servers (install any combination you plan to use):  
     - `npm install -g @modelcontextprotocol/server-serpapi` (SerpAPI)  
     - `npm install -g @modelcontextprotocol/server-tavily` (Tavily)  
     - `npm install -g @modelcontextprotocol/server-bing-web-search` (Bing Web Search)  
     Each of these servers requires its own API key—add them to `mcp_agent.secrets.yaml` and/or export `SERPAPI_API_KEY`, `TAVILY_API_KEY`, or `BING_SEARCH_V7_KEY` before running the benchmark.
2. Clone this repo, then create a venv inside the benchmark folder:

```bash
cd mas_traces/mcp_financial_analyzer_benchmark
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

The requirements file pins the `mcp-agent` package directly to the `feat/trace-collection` branch so you inherit all of the tracing fixes that were tested remotely.

## 2. Configure MCP + secrets

1. Copy the provided secrets template and insert your API keys:

```bash
cp mcp_agent.secrets.yaml.example mcp_agent.secrets.yaml
```

Add the keys for the provider you plan to use (OpenAI, Anthropic, etc.). The benchmark defaults to the Google Augmented LLM, so add `GOOGLE_API_KEY` to your environment or `.env` file as well.

### Using OpenAI or another LLM provider

The runner exposes a pluggable LLM layer so you can swap out Gemini when it misbehaves:

1. Populate `mcp_agent.secrets.yaml` with the provider you want to use. For OpenAI that means:

   ```yaml
   openai:
     api_key: "sk-..."
   ```

   Anthropic or any other supported backend follows the same nesting (`anthropic.api_key`, etc.).

2. Choose the backend at runtime in one of two ways:

   - CLI flag: `python main.py "Apple" --llm-backend openai`
   - Env var: `FINANCIAL_ANALYZER_LLM_BACKEND=openai python main.py`

   Built-in aliases include `google`/`gemini`, `openai`, and `anthropic`. For advanced cases you can pass your own class via `module.path:ClassName`.

3. (Optional) Pin a specific model if you do not want the defaults (`gemini-2.5-flash-lite` for Google, `gpt-4o` for OpenAI):

   - CLI flag: `--llm-model gpt-4o-mini`
   - Env var: `FINANCIAL_ANALYZER_LLM_MODEL=gpt-4o-mini`

When a non-Google backend is selected the script skips Gemini entirely, so you can continue benchmarking even if Gemini is degraded.

### Choosing a search provider (or multiple)

Headless Google Search is brittle because of frequent CAPTCHA enforcement, so the benchmark now supports multiple MCP-compatible search providers. Use whichever combination is most reliable in your environment:

1. Install and configure the search servers you care about (see the optional prerequisite list above). For each provider, add an entry to `mcp_agent.config.yaml`. Example snippets (uncomment the ones you use and swap the command/path if you installed them somewhere else):

```yaml
    # serpapi-search:
    #   command: "mcp-server-serpapi"
    #   env:
    #     SERPAPI_API_KEY: "${SERPAPI_API_KEY}"

    # tavily-search:
    #   command: "mcp-server-tavily"
    #   env:
    #     TAVILY_API_KEY: "${TAVILY_API_KEY}"

    # bing-search:
    #   command: "mcp-server-bing-web-search"
    #   env:
    #     BING_SEARCH_V7_KEY: "${BING_SEARCH_V7_KEY}"
```

2. Provide the API keys in `mcp_agent.secrets.yaml` (placeholders are included) or via environment variables.

3. Choose the order in which the benchmark should try those providers:
   - CLI flag: `--search-providers serpapi,bing`  
   - Env var: `FINANCIAL_ANALYZER_SEARCH_PROVIDERS=google,serpapi,tavily,bing`  
   The default order is `google,serpapi,tavily,bing`. The script filters out providers that are not configured in `mcp_agent.config.yaml` or missing API keys.

4. When multiple servers are available, the benchmark advertises all of them to the research agent. The LLM will try them in the order you list; if one fails (CAPTCHA, rate-limit, downtime) the agent automatically falls back to the next provider.

### Trace metadata

Every run now writes a metadata sidecar (`logs/financial_analyzer_traces-<timestamp>.jsonl.metadata.json`) that captures the runtime configuration: LLM backend/model, search provider order, sanity mode, CLI arguments, etc. This makes it easy to trace a log file back to the exact config that produced it.

To tag older trace files that were created before this feature landed, run:

```bash
cd mas_traces/mcp_financial_analyzer_benchmark
python scripts/backfill_trace_metadata.py --logs-dir logs
```

By default the script assumes all but the newest trace used the Google/Gemini backend and the newest trace used OpenAI GPT-4o. Override the `--older-*`, `--latest-*`, or `--search-providers` flags if your history differs. Metadata files are only created for trace files that don’t already have a `.metadata.json` neighbor unless you pass `--overwrite`.

2. Adjust `mcp_agent.config.yaml` if needed:
       - The config already points to `mcp-server-fetch`, `g-search-mcp`, and `mcp-server-filesystem`. If those binaries live elsewhere, update the `command` entries.
       - `g-search-mcp` is invoked via `bin/g-search-headless.mjs`, a small wrapper that forces Playwright to remain in headless mode and exports `PLAYWRIGHT_HEADLESS=1`. It also passes the global npm directory through both `NODE_PATH` and `G_SEARCH_GLOBAL_NODE_ROOT`, allowing the wrapper to resolve the globally installed `g-search-mcp` + `playwright` packages even though the script itself lives outside that tree. Together these tweaks keep the browser from trying to open a real X11 window (which would hang inside CI) and ensure all Node dependencies are found.
       - **Search reliability TODOs:** Google regularly injects CAPTCHA/consent flows that stall headless Playwright. Long term we should:
         1. Provide a documented “headed bootstrap” flow so users can solve the CAPTCHA once and copy the generated `browser-state-*.json` back to CI.
         2. Automate state rotation/reset so the cookies stay fresh (e.g., helper script that replays the headed flow as needed).
         3. Add randomized query timing + throttling knobs to reduce how often Google challenges us.
         4. Support alternative search MCPs (SerpAPI, Tavily, Bing) or a mock search backend for CI so we’re not coupled to Google Playwright at all.  
            _Status:_ you can already wire up any combination of these MCP servers and control the order with `--search-providers`. The remaining work is automating setup (headed bootstrap + cookie rotation).
       - OpenTelemetry is enabled with a file exporter that writes to `logs/financial_analyzer_traces-<timestamp>.jsonl`. You can add an OTLP exporter here if you want to stream traces to Jaeger/Honeycomb/etc.
       - **TODO:** Evaluate swapping `g-search-mcp` for an API-based search MCP (e.g., SerpAPI MCP, Tavily MCP, Bing Web Search MCP) so we no longer depend on Playwright + Google CAPTCHA. Whichever provider we choose should be pluggable via `mcp_agent.config.yaml` and documented with setup instructions + rate-limit guidance.

## 3. Run the benchmark manually

Run from this directory so the app can discover `mcp_agent.config.yaml` and `mcp_agent.secrets.yaml` automatically.

```bash
python main.py "Apple"
```

Environment knobs:

| Variable | Purpose |
| --- | --- |
| `FINANCIAL_ANALYZER_SANITY_MODE` | `1` (default) for the short sanity-check workflow, `0` for the full multi-agent deep dive |
| `GOOGLE_API_KEY` | Required for the default Google LLM path |
| `BENCHMARK_LLM_REQUESTS_PER_MIN` | Optional requests-per-minute throttle applied to the Gemini client (also honors `BENCHMARK_LLM_RATE_PERIOD`) |
| `FINANCIAL_ANALYZER_SEARCH_PROVIDERS` | (Optional) Comma-separated search MCP order, e.g. `serpapi,bing,google`. Matches the `--search-providers` CLI flag. |

Each run saves a markdown report under `company_reports/` and emits a unique OTEL trace file under `logs/`.
When the optional rate-limit variables are set, the script configures the built-in Google rate limiter so that even harness-driven stress tests stay under the desired calls-per-minute budget.

## 4. Batch runs via the benchmark harness

The shared runner (`mas_traces/run_benchmarks.py`) can execute this benchmark multiple times with a fixed timeout and automatic SIGKILL on overruns. Example:

```bash
cd mas_traces
python run_benchmarks.py --benchmark mcp_financial --runs 2 --timeout 900
```

The script prints which log files were created on every iteration so it is easy to archive or diff traces across runs.
