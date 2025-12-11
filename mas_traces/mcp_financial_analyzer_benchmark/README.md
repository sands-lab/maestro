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
# Add at least an OpenAI key + one search provider (Tavily, see below).

python main.py "Apple" --llm-backend openai --search-providers tavily
```

The CLI auto-loads `mcp_agent.config.yaml`/`mcp_agent.secrets.yaml`, seeds any missing `*_API_KEY` environment variables for the current process, runs the workflow, and restores your shell environment afterward.

---

## Requirements

- Python 3.10+
- Python dependencies:
  - `pip install -r requirements.txt` (installs `mcp-agent`, `mcp-server-fetch`, Tavily clients, etc.)
- API keys for your preferred LLM and search provider(s)

---

## Secrets and Environment

All API keys live in `mcp_agent.secrets.yaml`. Copy the example file, then add entries like:

```yaml
openai:
  api_key: "sk-..."
tavily:
  api_key: "tvly-..."
```

At startup the script reads this file and exports `${PROVIDER}_API_KEY` for each section (`tavily` → `TAVILY_API_KEY`, etc.) unless you already set the variable yourself. These temporary exports are removed when the program exits.

---

## Running a Local OTLP Collector

The repo ships a tiny OpenTelemetry Collector config (`otel-collector.local.yaml`) plus a helper launcher (`mas_traces/run_local_otel_collector.sh`). They expose the standard OTLP gRPC (`:4317`) and HTTP (`:4318`) ports, batch incoming spans, and dump everything to `collector_logs/financial_analyzer_spans.jsonl` while also echoing spans to stdout.

```bash
cd mas_traces/mcp_financial_analyzer_benchmark
# Requires Docker; override OTEL_COLLECTOR_IMAGE if you host your own build
../run_local_otel_collector.sh
```

If you already installed `otelcol-contrib`, run it directly instead:

```bash
otelcol-contrib --config=otel-collector.local.yaml
```

Once you see `Everything is ready. Begin running and processing data.`, point the benchmark at the collector:

```bash
python main.py "Parker-Hannifin Corporation" \
  --llm-backend openai \
  --search-providers tavily
```

To enable OpenTelemetry tracing, ensure the `otel` field is properly configured in `mcp_agent.config.yaml`:

```yaml
otel:
  exporters:
    - otlp:
        endpoint: "http://localhost:4318/v1/traces"
  sample_rate: 1.0
```

> If you want to disable sending to remote collector, just remove the `otlp` entry here.
> And bring back the `file` entry. (You can also use comment to do all these)

---

## Shipping OpenTelemetry Traces Remotely

The benchmark writes OpenTelemetry spans to `logs/financial_analyzer_traces-*.jsonl` by default (see `otel.exporters` in `mcp_agent.config.yaml`). If you want to stream those spans to a remote collector instead, supply an OTLP/HTTP endpoint at runtime:

```bash
python main.py "Parker-Hannifin Corporation" \
  --llm-backend openai \
  --search-providers tavily \
  --otel-remote-endpoint "http://localhost:4318/v1/traces" \
  --otel-remote-header "Authorization=Basic abc123"
```

- `--otel-remote-endpoint` (or `FINANCIAL_ANALYZER_OTEL_REMOTE_ENDPOINT`) switches the exporter to OTLP/HTTP for the current run.
- Repeat `--otel-remote-header KEY=VALUE` to add OTLP headers. You can also set `FINANCIAL_ANALYZER_OTEL_REMOTE_HEADERS="Authorization=Basic abc123,X-Project=mas-traces"` (comma or semicolon delimited) to seed headers from the environment.
- Leave the flag unset to continue writing JSONL traces locally.

---

## Running the Benchmark

```bash
python main.py "Apple" --llm-backend openai --search-providers tavily
```

- Reports land in `company_reports/<company>_report_<timestamp>.md`.
- Traces land in `logs/financial_analyzer_traces-<timestamp>.jsonl`.
- Metadata sidecars (same name + `.metadata.json`) record CLI args, LLM/search configuration, environment overrides, and whether the workflow completed successfully (plus any captured error message).

Key environment knobs:

| Variable | Purpose |
| --- | --- |
| `FINANCIAL_ANALYZER_SANITY_MODE` | `1` (default) for the short run, `0` for the full workflow |
| `BENCHMARK_LLM_REQUESTS_PER_MIN` + `BENCHMARK_LLM_RATE_PERIOD` | Optional rate limits when using Tavily |
| `FINANCIAL_ANALYZER_SEARCH_PROVIDERS` | Comma-separated priority list for search MCP servers |

---

## Troubleshooting Notes

- **Search flakiness:** Prefer Tavily when possible, or add multiple providers so the agent can fall back.
- **Environment sanity check:** If Tavily still complains about missing keys, run `python main.py --print-env-only` to verify the script can see the secrets before launching the workflow.

Feel free to adapt the MCP config for additional servers or exporters—everything is pluggable via `mcp_agent.config.yaml` and the secrets file.
