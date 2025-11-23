# MCP Financial Analyzer Benchmark

This standalone benchmark packages the `feat/trace-collection` version of Chen Ishi's [MCP financial analyzer](https://github.com/chenIshi/mcp-agent/tree/feat/trace-collection/examples/usecases/mcp_financial_analyzer). It keeps the original OpenTelemetry instrumentation (per-run JSONL files in `logs/`) but swaps the setup instructions to use standard Python virtual environments instead of `uv`.

## 1. Prerequisites

1. System dependencies  
   - Python 3.10+  
   - Node.js for MCP servers (`g-search-mcp`, `mcp-server-filesystem`)  
   - `npm install -g g-search-mcp @modelcontextprotocol/server-filesystem`  
   - Install the fetch MCP server (Python): `pip install mcp-server-fetch`
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

2. Adjust `mcp_agent.config.yaml` if needed:
   - The config already points to `mcp-server-fetch`, `g-search-mcp`, and `mcp-server-filesystem`. If those binaries live elsewhere, update the `command` entries.
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

Each run saves a markdown report under `company_reports/` and emits a unique OTEL trace file under `logs/`.

## 4. Batch runs via the benchmark harness

The shared runner (`mas_traces/run_benchmarks.py`) can execute this benchmark multiple times with a fixed timeout and automatic SIGKILL on overruns. Example:

```bash
cd mas_traces
python run_benchmarks.py --benchmark mcp_financial --runs 2 --timeout 900
```

The script prints which log files were created on every iteration so it is easy to archive or diff traces across runs.
