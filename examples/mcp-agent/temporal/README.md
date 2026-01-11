# Temporal MCP Workflow Testbench

| Framework | Description |
| --- | --- |
| mcp-agent + Temporal | Durable workflow orchestration that exposes workflows and synchronous tools over MCP, ready for MAS logging. |

This directory builds upon the upstream Temporal example provided by the [mcp-agent repository](https://github.com/lastmile-ai/mcp-agent/tree/04cae7ea5ba3a5c4e62c22884938699c73705651/examples/cloud/temporal).

This testbench is designed to help users understand and experiment with Temporal's durable workflow orchestration capabilities in the context of MCP (Model Context Protocol). By leveraging this setup, you can:

- Run and observe workflows locally using a Temporal development server.
- Experiment with various workflow patterns and tools exposed via MCP.

---

## Quick Start

```bash
cd examples/mcp-agent/temporal

# Install dependencies (UV reads pyproject.toml)
uv sync

# Configure OpenAI (and any other) secrets
cp mcp_agent.secrets.yaml.example mcp_agent.secrets.yaml
$EDITOR mcp_agent.secrets.yaml

# Start a local Temporal dev server in another terminal
temporal server start-dev

# Terminal A: run the worker so workflows can execute
uv run temporal_worker.py

# Terminal B: launch the MCP server (exposes workflows + tools)
uv run main.py
```

Point [MCP Inspector](https://github.com/modelcontextprotocol/inspector) or another MCP client at `http://127.0.0.1:8000/sse` to drive the workflows. If you need to deploy this to mcp-agent Cloud, see the deployment notes near the end of this file.

---

## Secrets & Configuration

- Secrets live in `mcp_agent.secrets.yaml`. Copy the example file and provide an OpenAI key:

  ```yaml
  openai:
    api_key: "sk-..."
  ```

- `mcp_agent.config.yaml` already targets a local Temporal dev cluster (`localhost:7233`) and registers the in-repo workflows. Update `temporal.host`, `namespace`, or `task_queue` if you test against a remote cluster.
- Extra activity modules can be preloaded by uncommenting `workflow_task_modules`.

---

## Observability

- **Structured logs** – `logger.transports` includes console + file logging. JSONL logs land in `logs/temporal_workflow_logs-<timestamp>.jsonl`.
- **OpenTelemetry spans** – The `otel` section ships with a file exporter so every run writes spans to `logs/temporal_traces-<timestamp>.jsonl`. Swap the exporter to OTLP in `mcp_agent.config.yaml` if you want to stream directly to a collector.
- **Local collector** – To visualize spans, reuse the MAS helper:

  ```bash
  cd examples/mcp-agent/temporal
  ../run_local_otel_collector.sh   # requires Docker
  ```

  Update `mcp_agent.config.yaml`’s `otel.exporters` to include the OTLP endpoint (`http://localhost:4318/v1/traces`) when you want live streaming instead of JSONL dumps.

---

## Temporal Workflow Surface

The server exposes both workflow-oriented tools and helper utilities via MCP:

- `workflows-list` – enumerate registered workflows.
- `workflows-BasicAgentWorkflow-run` / `finder_tool` – run the agent-driven workflow immediately, returning the final response.
- `workflows-PauseResumeWorkflow-run` plus `workflows-resume` – demonstrate signaling; the pause workflow blocks until you resume it via the tool or the Temporal UI.
- `workflows-SamplingWorkflow-run`, `workflows-ElicitationWorkflow-run`, and `workflows-NotificationsWorkflow-run` – cover direct sampling, elicitation, and notification forwarding through the MCP session proxy.
- `workflows--get_status` / `workflows-cancel` – query or cancel active runs.

Use the Temporal Web UI at `http://localhost:8233` to inspect histories, send signals, and confirm worker heartbeats during your testbench sessions.

---

## Deploy to mcp-agent Cloud

Cloud deployment follows the upstream instructions:

```bash
uv run mcp-agent login
uv run mcp-agent deploy temporal_example
```

The CLI bundles this directory, uploads it, and returns an HTTPS SSE endpoint you can plug into MCP Inspector or any MAS client.

## Code Structure

- `main.py` - Defines the workflows and creates the MCP server
- `temporal_worker.py` - For local testing only. Sets up a Temporal worker to process local workflow tasks
- `mcp_agent.config.yaml` - Configuration for MCP servers and the Temporal execution engine
- `mcp_agent.secrets.yaml` - Contains API keys (not included in repository)
