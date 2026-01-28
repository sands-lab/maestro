#!/usr/bin/env bash
set -euo pipefail

# Run experiment script for dist_vs_monolithic_without_kagent
# Local (non-containerized) mode only. Uses real LLM config from local_deployment/.env.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$SCRIPT_DIR"

# Configuration
ITERATIONS="${ITERATIONS:-1}"                    # Number of runs (default: 1)
INPUT_FILE="${INPUT_FILE:-$SCRIPT_DIR/inputs.txt}"
INPUT_COUNT="${INPUT_COUNT:-1000}"
REGENERATE_INPUTS="${REGENERATE_INPUTS:-false}"

# Result directory
RESULT_ROOT="$SCRIPT_DIR/results"
TS="$(date +%Y%m%d_%H%M%S)"
EXP_DIR="$RESULT_ROOT/exp_${TS}_local"
LOG_DIR="$EXP_DIR/logs"
mkdir -p "$EXP_DIR" "$LOG_DIR"

echo "=========================================="
echo "Multi-Agent System Experiment"
echo "=========================================="
echo "Mode: local"
echo "Iterations: $ITERATIONS"
echo "Input file: $INPUT_FILE (random per run)"
echo "Results: $EXP_DIR"
echo "=========================================="

cleanup_local() {
  if [[ -d "$SCRIPT_DIR/local_deployment/pids" ]] && ls "$SCRIPT_DIR/local_deployment/pids"/*.pid >/dev/null 2>&1; then
    "$SCRIPT_DIR/local_deployment/stop_local_agents.sh" || true
  fi
}

generate_inputs() {
  local output=$1
  local count=$2

  python3 - "$output" "$count" <<'PY'
import random
import sys
from pathlib import Path

output = Path(sys.argv[1])
count = int(sys.argv[2])
random.seed()

formats = [
    "a concise LinkedIn post",
    "a short blog outline",
    "a tweet thread",
    "an email newsletter intro",
    "a product announcement blurb",
    "a FAQ section",
    "a step-by-step guide",
    "a troubleshooting checklist",
    "a case study summary",
    "a release note",
    "a marketing tagline",
    "a landing page hero section",
    "a workshop agenda",
    "a project kickoff brief",
    "a user story",
    "a comparison table summary",
    "a startup pitch paragraph",
    "a press release intro",
    "a knowledge base article",
    "a short explainer script",
]

topics = [
    "agent observability",
    "multi-agent coordination",
    "prompt evaluation",
    "data privacy in AI",
    "latency optimization",
    "LLM cost control",
    "model selection",
    "experiment reproducibility",
    "prompt safety",
    "tool calling best practices",
    "error handling in agents",
    "trace sampling",
    "metrics vs traces",
    "A/B testing prompts",
    "embedding search",
    "RAG pipelines",
    "vector database choice",
    "real-time monitoring",
    "incident response",
    "load testing",
    "token budgeting",
    "context window limits",
    "fine-tuning tradeoffs",
    "evaluation harnesses",
    "hallucination mitigation",
    "schema validation",
    "multi-step reasoning",
    "agent routing",
    "fallback strategies",
    "human-in-the-loop review",
    "API rate limits",
    "sandboxing tools",
    "prompt caching",
    "streaming responses",
    "goal decomposition",
    "agent memory",
    "trace visualization",
    "metrics dashboards",
    "cost attribution",
    "trace correlation",
    "incident postmortems",
]

audiences = [
    "product managers",
    "software engineers",
    "ML engineers",
    "CTOs",
    "startup founders",
    "SRE teams",
    "data scientists",
    "security teams",
    "developer advocates",
    "enterprise buyers",
    "open-source maintainers",
    "QA engineers",
    "platform teams",
    "tech leads",
    "operations teams",
]

tones = [
    "practical",
    "persuasive",
    "technical",
    "friendly",
    "executive",
    "direct",
    "curious",
    "optimistic",
    "cautious",
    "actionable",
]

constraints = [
    "Keep it under 120 words.",
    "Include 3 bullet points.",
    "Add a short call to action.",
    "Avoid jargon.",
    "Use one emoji.",
    "Include a single metric example.",
    "Use active voice.",
    "Include a short analogy.",
    "End with a question.",
    "Focus on outcomes, not features.",
]

prefixes = [
    "Write",
    "Draft",
    "Create",
    "Outline",
    "Summarize",
    "Generate",
    "Compose",
    "Produce",
    "Craft",
    "Build",
]

unique = set()
max_attempts = count * 20

for _ in range(max_attempts):
    if len(unique) >= count:
        break
    prompt = (
        f"{random.choice(prefixes)} {random.choice(formats)} about {random.choice(topics)} "
        f"for {random.choice(audiences)} with a {random.choice(tones)} tone. "
        f"{random.choice(constraints)}"
    )
    unique.add(prompt)

if len(unique) < count:
    raise SystemExit(f"Only generated {len(unique)} prompts; increase pools.")

output.write_text("\n".join(sorted(unique)) + "\n", encoding="utf-8")
PY
}

pick_random_message() {
  local input=$1

  python3 - "$input" <<'PY'
import random
import sys

path = sys.argv[1]
with open(path, "r", encoding="utf-8") as handle:
    lines = [line.strip() for line in handle if line.strip()]

if not lines:
    raise SystemExit("Input file is empty.")

random.seed()
print(random.choice(lines))
PY
}

trap 'cleanup_local' EXIT

get_env_var() {
  # read from local_deployment/.env or .env.example
  local key=$1 default=$2
  local env_file="$SCRIPT_DIR/local_deployment/.env"
  [[ -f "$env_file" ]] || env_file="$SCRIPT_DIR/local_deployment/.env.example"
  local val
  val=$(grep -E "^${key}=" "$env_file" 2>/dev/null | tail -n1 | cut -d '=' -f2-)
  if [[ -n "${val:-}" ]]; then
    echo "$val"
  else
    echo "$default"
  fi
}

run_local() {
  echo ""
  echo ">>> Starting local agents (non-containerized)"
  echo ">>> Using real LLM config from local_deployment/.env"
  if [[ "$REGENERATE_INPUTS" == "true" || "$REGENERATE_INPUTS" == "1" || ! -f "$INPUT_FILE" ]]; then
    echo ">>> Generating $INPUT_COUNT random inputs at $INPUT_FILE"
    generate_inputs "$INPUT_FILE" "$INPUT_COUNT"
  fi

  for i in $(seq 1 $ITERATIONS); do
      echo ""
      echo ">>> [local] Run $i/$ITERATIONS"

      # Cleanup previous run
      cleanup_local

      # Generate unified timestamp for this run (all agents will use the same timestamp)
      # This ensures files from the same run can be grouped accurately without time windows
      UNIFIED_TIMESTAMP=$(date +%Y%m%d_%H%M%S)
      export OTEL_RUN_TIMESTAMP="$UNIFIED_TIMESTAMP"
      echo ">>> Using unified timestamp for this run: $UNIFIED_TIMESTAMP"

      # Clear old traces/metrics from agent directories only (not from root)
      # This ensures each run starts fresh in agent directories, but root directories accumulate
      # The root traces/ and metrics/ directories accumulate all runs for analysis
      echo ">>> Clearing old traces and metrics from agent directories (root directories preserve all runs)..."
      for agent_dir in \
        "$SCRIPT_DIR/src/agents/content_planner" \
        "$SCRIPT_DIR/src/agents/content_writer" \
        "$SCRIPT_DIR/src/agents/content_editor" \
        "$SCRIPT_DIR/src/hosts/coordinator"; do
        if [[ -d "$agent_dir/traces" ]]; then
          rm -f "$agent_dir/traces"/*.jsonl 2>/dev/null || true
        fi
        if [[ -d "$agent_dir/metrics" ]]; then
          rm -f "$agent_dir/metrics"/*.jsonl 2>/dev/null || true
        fi
      done

      # Start agents (OTEL_RUN_TIMESTAMP will be passed via environment)
      (cd "$SCRIPT_DIR/local_deployment" && ./start_local_agents.sh)

      local coord_port
      coord_port=$(get_env_var "COORDINATOR_PORT" "8093")

      # Health check loop
      echo ">>> Waiting for coordinator to be ready on port $coord_port..."
      for retry in {1..30}; do
        if curl -s "http://127.0.0.1:${coord_port}/" >/dev/null; then
          echo ">>> Coordinator is ready!"
          break
        fi
        echo ">>> Waiting for coordinator... ($retry/30)"
        sleep 2
      done

      # Send request
      echo ">>> Sending request..."
      local run_message
      run_message="$(pick_random_message "$INPUT_FILE")"
      echo ">>> Message: $run_message"
      result_file="$EXP_DIR/run_${i}_local.json"
      if python3 "$SCRIPT_DIR/send_request.py" \
          --coordinator "http://127.0.0.1:${coord_port}" \
          --message "$run_message" \
          --json > "$result_file" 2>&1; then
        echo ">>> ✓ Run $i completed successfully"
      else
        echo ">>> ✗ Run $i failed (check $result_file)"
      fi

      # Collect traces and metrics
      echo ">>> Collecting traces and metrics..."
      local_run_dir="$EXP_DIR/run_${i}_data"
      mkdir -p "$local_run_dir"

      # Copy traces and metrics to project root (like marketing-agency)
      # This allows direct analysis from project root without going into subdirectories
      unified_traces_dir="$SCRIPT_DIR/traces"
      unified_metrics_dir="$SCRIPT_DIR/metrics"
      mkdir -p "$unified_traces_dir" "$unified_metrics_dir"

      # Copy traces and metrics from all agents (both to run-specific dir and unified dir)
      for agent_dir in \
        "$SCRIPT_DIR/src/agents/content_planner" \
        "$SCRIPT_DIR/src/agents/content_writer" \
        "$SCRIPT_DIR/src/agents/content_editor" \
        "$SCRIPT_DIR/src/hosts/coordinator"; do
        agent_name=$(basename "$agent_dir")

        # Copy to run-specific directory
        if [[ -d "$agent_dir/traces" ]]; then
          cp -r "$agent_dir/traces" "$local_run_dir/${agent_name}_traces" 2>/dev/null || true
        fi
        if [[ -d "$agent_dir/metrics" ]]; then
          cp -r "$agent_dir/metrics" "$local_run_dir/${agent_name}_metrics" 2>/dev/null || true
        fi

        # Copy to unified directories for visualization
        if [[ -d "$agent_dir/traces" ]]; then
          cp "$agent_dir/traces"/*.jsonl "$unified_traces_dir/" 2>/dev/null || true
        fi
        if [[ -d "$agent_dir/metrics" ]]; then
          cp "$agent_dir/metrics"/*.jsonl "$unified_metrics_dir/" 2>/dev/null || true
        fi
      done

      # Note: Root traces and metrics are already copied from agent directories above
      # No need to copy again from root directories to avoid duplication

      # Move logs
      if [ -d "$SCRIPT_DIR/local_deployment/logs" ]; then
        mkdir -p "$local_run_dir/logs"
        mv "$SCRIPT_DIR/local_deployment/logs/"* "$local_run_dir/logs/" 2>/dev/null || true
      fi

      cleanup_local

      if [[ $i -lt $ITERATIONS ]]; then
        echo ">>> Waiting 5 seconds before next run..."
        sleep 5
      fi
  done
}

# Main execution
run_local

echo ""
echo "=========================================="
echo "Experiment Complete!"
echo "=========================================="
echo "Results directory: $EXP_DIR"
echo ""
echo "Data locations:"
echo "  - Traces: $SCRIPT_DIR/traces/"
echo "  - Metrics: $SCRIPT_DIR/metrics/"
echo "  - Per-run detailed data: $EXP_DIR/run_*_data/"
echo ""
echo "To visualize metrics (from project root):"
echo "  cd $SCRIPT_DIR"
echo "  python3 plot_example_metrics.py --mode per_run"
echo ""
