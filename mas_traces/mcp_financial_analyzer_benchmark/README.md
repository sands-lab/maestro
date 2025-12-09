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

### Using Google LLMs

You can run Gemini through either the public API key or Vertex AI:

| Option | Steps |
| --- | --- |
| **API key** (default) | Add `google.api_key: "AIza..."` to `mcp_agent.secrets.yaml`. |
| **Vertex AI service account** | 1) Place your JSON key anywhere under the repo (e.g., `.google-service-account.json`). 2) Export `GOOGLE_APPLICATION_CREDENTIALS` before running: `export GOOGLE_APPLICATION_CREDENTIALS=\"$PWD/mas_traces/mcp_financial_analyzer_benchmark/.google-service-account.json\"`. 3) Update `mcp_agent.config.yaml` → `google:` block with `vertexai: true`, plus the `project` and `location` that host the Gemini model. |

When `vertexai: true`, the agent constructs the Google client with Vertex AI credentials and ignores `google.api_key`. Make sure the service account has Vertex AI permissions (`roles/aiplatform.user` or finer-grained equivalents).

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
- Metadata sidecars (same name + `.metadata.json`) record CLI args, LLM/search configuration, environment overrides, and whether the workflow completed successfully (plus any captured error message).

Key environment knobs:

| Variable | Purpose |
| --- | --- |
| `FINANCIAL_ANALYZER_SANITY_MODE` | `1` (default) for the short run, `0` for the full workflow |
| `BENCHMARK_LLM_REQUESTS_PER_MIN` + `BENCHMARK_LLM_RATE_PERIOD` | Optional rate limits when using Gemini |
| `FINANCIAL_ANALYZER_SEARCH_PROVIDERS` | Comma-separated priority list for search MCP servers |

If you switch between API-key and Vertex-based Gemini frequently, keep both configurations handy: comment/uncomment the `google.api_key` entry in `mcp_agent.secrets.yaml` and flip `google.vertexai` in `mcp_agent.config.yaml` as needed. The workflow reads those settings on every run, so no code changes are required.

---

## Trace Utilities

- **Backfill metadata** for older trace files:

  ```bash
  python scripts/backfill_trace_metadata.py --logs-dir logs
  ```

  Supply `--status failed` or `--workflow-incomplete` if you're recreating metadata for a run that did not finish cleanly.

- **Publishable trace bundle**: create redacted copies of every JSONL/metadata pair without touching the originals.

  ```bash
  python scripts/sanitize_logs.py --source logs --dest logs_clean
  ```

  The sanitizer scrubs known key names (e.g., `*_API_KEY`, `authorization`, PEM blocks) and patterns like `sk-...` or `AIza...`, then writes the cleaned files to `logs_clean/`. Share only the sanitized directory; keep `logs/` private. Pass `--start-date YYYYMMDD` and/or `--end-date YYYYMMDD` (optionally add `_HHMMSS`) to limit which trace timestamps are processed, e.g.:

  ```bash
  python scripts/sanitize_logs.py \
    --source logs \
    --dest logs_clean \
    --start-date 20251201 \
    --end-date 20251201
  ```

---

## Running a Local OTLP Collector

The repo ships a tiny OpenTelemetry Collector config (`otel-collector.local.yaml`) plus a helper launcher (`scripts/run_local_otel_collector.sh`). They expose the standard OTLP gRPC (`:4317`) and HTTP (`:4318`) ports, batch incoming spans, and dump everything to `collector_logs/financial_analyzer_spans.jsonl` while also echoing spans to stdout.

```bash
cd mas_traces/mcp_financial_analyzer_benchmark
# Requires Docker; override OTEL_COLLECTOR_IMAGE if you host your own build
./scripts/run_local_otel_collector.sh
```

If you already installed `otelcol-contrib`, run it directly instead:

```bash
otelcol-contrib --config=otel-collector.local.yaml
```

Once you see `Everything is ready. Begin running and processing data.`, point the benchmark at the collector (the example below pins the Google/Gemini backend and Tavily search so it runs out-of-the-box without Bing credentials):

```bash
python main.py "Parker-Hannifin Corporation" \
  --llm-backend google \
  --llm-model gemini-2.0-flash-lite \
  --search-providers tavily \
  --otel-remote-endpoint "http://localhost:4318/v1/traces"
```

Any other OTLP-compatible producer (the sample MCP server under `mcp_agent` included) can send traces to the very same collector. For remote deployments, copy `otel-collector.local.yaml` to the target host, tweak `collector_logs` to a writable directory, and run `otelcol[-contrib] --config /path/to/config`. Then launch the benchmark with `--otel-remote-endpoint http://<host>:4318/v1/traces` (or the gRPC variant `grpc://<host>:4317`).

---

## Shipping OpenTelemetry Traces Remotely

The benchmark writes OpenTelemetry spans to `logs/financial_analyzer_traces-*.jsonl` by default (see `otel.exporters` in `mcp_agent.config.yaml`). If you want to stream those spans to a remote collector instead, supply an OTLP/HTTP endpoint at runtime:

```bash
python main.py "Parker-Hannifin Corporation" \
  --llm-backend google \
  --llm-model gemini-2.0-flash-lite \
  --search-providers tavily \
  --otel-remote-endpoint "http://localhost:4318/v1/traces" \
  --otel-remote-header "Authorization=Basic abc123"
```

- `--otel-remote-endpoint` (or `FINANCIAL_ANALYZER_OTEL_REMOTE_ENDPOINT`) switches the exporter to OTLP/HTTP for the current run.
- Repeat `--otel-remote-header KEY=VALUE` to add OTLP headers. You can also set `FINANCIAL_ANALYZER_OTEL_REMOTE_HEADERS="Authorization=Basic abc123,X-Project=mas-traces"` (comma or semicolon delimited) to seed headers from the environment.
- Leave the flag unset to continue writing JSONL traces locally.

This mirrors the [mcp-agent tracing example](https://github.com/lastmile-ai/mcp-agent/blob/4af1e558e47825da1dfa4aeb42cbd411e571926a/examples/tracing/mcp/README.md): the configuration stays local-first, but you can override it on demand without editing `mcp_agent.config.yaml`.

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
