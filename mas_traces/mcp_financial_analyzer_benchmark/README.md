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

2. Adjust `mcp_agent.config.yaml` if needed:
   - The config already points to `mcp-server-fetch`, `g-search-mcp`, and `mcp-server-filesystem`. If those binaries live elsewhere, update the `command` entries.
   - `g-search-mcp` is invoked via `bin/g-search-headless.mjs`, a small wrapper that forces Playwright to remain in headless mode and exports `PLAYWRIGHT_HEADLESS=1`. It also passes the global npm directory through both `NODE_PATH` and `G_SEARCH_GLOBAL_NODE_ROOT`, allowing the wrapper to resolve the globally installed `g-search-mcp` + `playwright` packages even though the script itself lives outside that tree. Together these tweaks keep the browser from trying to open a real X11 window (which would hang inside CI) and ensure all Node dependencies are found.
   - OpenTelemetry is enabled with a file exporter that writes to `logs/financial_analyzer_traces-<timestamp>.jsonl`. You can add an OTLP exporter here if you want to stream traces to Jaeger/Honeycomb/etc.

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

Each run saves a markdown report under `company_reports/` and emits a unique OTEL trace file under `logs/`.
When the optional rate-limit variables are set, the script configures the built-in Google rate limiter so that even harness-driven stress tests stay under the desired calls-per-minute budget.

## 4. Batch runs via the benchmark harness

The shared runner (`mas_traces/run_benchmarks.py`) can execute this benchmark multiple times with a fixed timeout and automatic SIGKILL on overruns. Example:

```bash
cd mas_traces
python run_benchmarks.py --benchmark mcp_financial --runs 2 --timeout 900
```

The script prints which log files were created on every iteration so it is easy to archive or diff traces across runs.
