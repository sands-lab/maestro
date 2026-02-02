"""
Stock Analyzer with Enhanced Agent Prompts
--------------------------------------------------------------------------------
An integrated financial analysis tool using comprehensive, structured agent prompts
from the portfolio analyzer example.
"""

import argparse
import asyncio
import glob
import importlib
import json
import os
import re
import signal
import sys
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any
from pathlib import Path
import yaml
from mcp_agent.app import MCPApp
from mcp_agent.config import (
    GoogleSettings,
    Settings,
    LoggerSettings,
    MCPSettings,
    MCPServerSettings,
    get_settings,
)
from mcp_agent.agents.agent import Agent
from mcp_agent.workflows.orchestrator.orchestrator import Orchestrator
from mcp_agent.workflows.llm.augmented_llm import RequestParams
from mcp_agent.workflows.evaluator_optimizer.evaluator_optimizer import (
    EvaluatorOptimizerLLM,
    QualityRating,
)

from maestro.telemetry_helpers.system_metrics import SystemMetricsMonitor

# Configuration values
OUTPUT_DIR = "company_reports"
TRACE_LOG_DIR = "logs"
COLLECTOR_LOG_SYMLINK = Path("collector_logs/financial_analyzer_spans.jsonl")
COLLECTOR_LOG_PATTERN = re.compile(
    r"financial_analyzer_spans-(?P<run_id>\d{8}_\d{6})\.jsonl"
)

LLM_BACKEND_REGISTRY = {
    "google": {
        "module": "mcp_agent.workflows.llm.augmented_llm_google",
        "class": "GoogleAugmentedLLM",
        "config_attr": "google",
    },
    "openai": {
        "module": "mcp_agent.workflows.llm.augmented_llm_openai",
        "class": "OpenAIAugmentedLLM",
        "config_attr": "openai",
    },
    "anthropic": {
        "module": "mcp_agent.workflows.llm.augmented_llm_anthropic",
        "class": "AnthropicAugmentedLLM",
        "config_attr": "anthropic",
    },
}

LLM_BACKEND_ALIASES = {
    "gemini": "google",
}

DEFAULT_SEARCH_PROVIDER_CHAIN = ["google", "tavily", "bing"]

SEARCH_PROVIDER_ALIASES = {
    "google": "g-search",
    "g-search": "g-search",
    "gsearch": "g-search",
    "g_search": "g-search",
    "tavily": "tavily-search",
    "bing": "bing-search",
    "bing-web": "bing-search",
    "bing_search": "bing-search",
}

SEARCH_PROVIDER_LABELS = {
    "g-search": "Google Search (Playwright)",
    "tavily-search": "Tavily",
    "bing-search": "Bing Web Search",
}

SEARCH_PROVIDER_TOOL_HINTS = {
    "g-search": (
        "Google Search (Playwright): call `search` to run live Google queries; "
        "provide well-formed prompts and follow up with `fetch` for full pages."
    ),
    "tavily-search": (
        "Tavily: call `tavily_search` (set `topic` to `finance` or `news` and tighten "
        "`time_range` as needed) for SERPs, then `tavily_extract` for article text "
        "or `tavily_crawl`/`tavily_map` for site sweeps."
    ),
    "bing-search": (
        "Bing Web Search: call the `search` tool for SERPs and combine with `fetch` "
        "to pull the referenced articles."
    ),
}

METADATA_VERSION = 1

ENVIRONMENT_OVERRIDES = [
    "FINANCIAL_ANALYZER_SANITY_MODE",
    "FINANCIAL_ANALYZER_LLM_BACKEND",
    "FINANCIAL_ANALYZER_LLM_MODEL",
    "FINANCIAL_ANALYZER_SEARCH_PROVIDERS",
    "BENCHMARK_LLM_REQUESTS_PER_MIN",
    "BENCHMARK_LLM_RATE_PERIOD",
    "GOOGLE_API_KEY",
    "FINANCIAL_ANALYZER_OTEL_REMOTE_ENDPOINT",
    "FINANCIAL_ANALYZER_OTEL_REMOTE_HEADERS",
]

INTERRUPTED_BY_USER = False
_PREVIOUS_SIGINT_HANDLER = None


def _parse_cli_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the MCP financial analyzer with a selectable LLM backend."
    )
    parser.add_argument(
        "company",
        nargs="?",
        default=os.getenv("FINANCIAL_ANALYZER_COMPANY", "Apple"),
        help="Company ticker/name to analyze (default: Apple).",
    )
    parser.add_argument(
        "--llm-backend",
        default=os.getenv("FINANCIAL_ANALYZER_LLM_BACKEND", "google"),
        help=(
            "LLM backend alias (google, gemini, openai, anthropic) or "
            "a fully-qualified import path in the form 'module.path:ClassName'."
        ),
    )
    parser.add_argument(
        "--llm-model",
        default=os.getenv("FINANCIAL_ANALYZER_LLM_MODEL"),
        help="Optional model override for the selected backend.",
    )
    parser.add_argument(
        "--search-providers",
        default=os.getenv("FINANCIAL_ANALYZER_SEARCH_PROVIDERS"),
        help=(
            "Comma-separated list that sets the preferred search MCP order "
            "(google, tavily, bing). Defaults to trying all in that order."
        ),
    )
    parser.add_argument(
        "--print-env-only",
        action="store_true",
        help="Print the seeded API key environment variables and exit.",
    )
    parser.add_argument(
        "--otel-remote-endpoint",
        default=os.getenv("FINANCIAL_ANALYZER_OTEL_REMOTE_ENDPOINT"),
        help=(
            "Optional OTLP/HTTP endpoint for OpenTelemetry spans. When provided, "
            "local file exporters are replaced with a remote OTLP exporter."
        ),
    )
    parser.add_argument(
        "--otel-remote-header",
        dest="otel_remote_headers",
        action="append",
        default=None,
        metavar="KEY=VALUE",
        help=(
            "Extra header to attach when sending OTLP traces. Repeat for multiple headers."
        ),
    )
    return parser.parse_args(argv)


def _load_secret_file() -> dict[str, Any]:
    candidates = [
        Path("mcp_agent.secrets.yaml"),
        Path("mcp-agent.secrets.yaml"),
        Path(__file__).with_name("mcp_agent.secrets.yaml"),
        Path(__file__).with_name("mcp-agent.secrets.yaml"),
    ]
    for candidate in candidates:
        if candidate.exists():
            try:
                return yaml.safe_load(candidate.read_text(encoding="utf-8")) or {}
            except Exception:
                return {}
    return {}


_SEEDED_ENV_VARS: dict[str, str | None] = {}


def _seed_env_from_secrets():
    secrets = _load_secret_file()
    if not secrets:
        return
    for secret_key, value in secrets.items():
        env_var = f"{secret_key.upper()}_API_KEY"
        if env_var in os.environ:
            continue
        if isinstance(value, dict):
            api_key = value.get("api_key")
        else:
            api_key = value
        if api_key:
            _SEEDED_ENV_VARS[env_var] = os.environ.get(env_var)
            os.environ[env_var] = api_key


_seed_env_from_secrets()


def _restore_seeded_env():
    for env_var, previous in _SEEDED_ENV_VARS.items():
        if previous is None:
            os.environ.pop(env_var, None)
        else:
            os.environ[env_var] = previous


def _install_interrupt_tracker():
    """Track Ctrl+C so metadata can record user-triggered interrupts."""
    global _PREVIOUS_SIGINT_HANDLER
    if _PREVIOUS_SIGINT_HANDLER is not None:
        return

    previous = signal.getsignal(signal.SIGINT)

    def _handler(signum, frame):
        global INTERRUPTED_BY_USER
        INTERRUPTED_BY_USER = True
        if callable(previous):
            previous(signum, frame)
        else:
            raise KeyboardInterrupt()

    _PREVIOUS_SIGINT_HANDLER = previous
    signal.signal(signal.SIGINT, _handler)


def _remove_interrupt_tracker():
    global _PREVIOUS_SIGINT_HANDLER
    if _PREVIOUS_SIGINT_HANDLER is None:
        return
    signal.signal(signal.SIGINT, _PREVIOUS_SIGINT_HANDLER)
    _PREVIOUS_SIGINT_HANDLER = None


def _print_seeded_env():
    if not _SEEDED_ENV_VARS:
        print("No API keys were seeded from secrets.")
        return
    print("Seeded API key environment variables:")
    for key in sorted(_SEEDED_ENV_VARS):
        value = os.environ.get(key)
        print(f"  {key} = {value}")

CLI_ARGS = _parse_cli_args()
COMPANY_NAME = CLI_ARGS.company
LLM_BACKEND = CLI_ARGS.llm_backend
LLM_MODEL_OVERRIDE = CLI_ARGS.llm_model
REQUESTED_SEARCH_PROVIDERS = CLI_ARGS.search_providers
PRINT_ENV_ONLY = CLI_ARGS.print_env_only
OTEL_REMOTE_ENDPOINT = (CLI_ARGS.otel_remote_endpoint or "").strip() or None
OTEL_REMOTE_HEADERS_RAW = CLI_ARGS.otel_remote_headers or []

env_remote_headers = os.getenv("FINANCIAL_ANALYZER_OTEL_REMOTE_HEADERS")
if env_remote_headers:
    separator_normalized = env_remote_headers.replace(";", ",")
    extra_tokens = [
        token.strip()
        for token in separator_normalized.split(",")
        if token and token.strip()
    ]
    OTEL_REMOTE_HEADERS_RAW.extend(extra_tokens)


def _log_message(logger, level: str, message: str, *args):
    formatted = message % args if args else message
    if logger is None:
        print(f"[otel:{level}] {formatted}")
    else:
        log_fn = getattr(logger, level, None)
        if callable(log_fn):
            log_fn(formatted)
        else:
            logger.info(formatted)


def _parse_remote_header_pairs(raw_pairs: list[str], logger) -> dict[str, str]:
    headers: dict[str, str] = {}
    for pair in raw_pairs:
        if "=" not in pair:
            _log_message(
                logger,
                "warning",
                "Ignoring malformed OTLP header '%s'; expected KEY=VALUE format.",
                pair,
            )
            continue
        key, value = pair.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            _log_message(
                logger,
                "warning",
                "Ignoring OTLP header with empty key from token '%s'.",
                pair,
            )
            continue
        headers[key] = value
    return headers


def _configure_otel_exporters(config, logger=None) -> bool:
    otel_config = getattr(config, "otel", None)
    if not otel_config or not getattr(otel_config, "enabled", False):
        return True

    if not OTEL_REMOTE_ENDPOINT:
        return True

    headers = _parse_remote_header_pairs(OTEL_REMOTE_HEADERS_RAW, logger)
    sanitized_endpoint = OTEL_REMOTE_ENDPOINT
    otlp_settings = {"endpoint": sanitized_endpoint}
    if headers:
        otlp_settings["headers"] = headers

    otel_config.exporters = [{"otlp": otlp_settings}]
    _log_message(
        logger,
        "info",
        "OpenTelemetry exporters configured for remote OTLP endpoint: %s",
        sanitized_endpoint,
    )
    return True


def _canonical_backend_name(backend: str | None) -> str | None:
    if not backend:
        return None
    backend = backend.strip()
    if not backend or ":" in backend:
        return None
    normalized = backend.lower()
    return LLM_BACKEND_ALIASES.get(normalized, normalized)


def _load_llm_factory(backend: str | None):
    """
    Return the LLM factory class + canonical backend key (if known).
    Supports either friendly aliases or fully-qualified module paths.
    """
    backend = (backend or "google").strip()
    if not backend:
        backend = "google"

    if ":" in backend:
        module_name, class_name = backend.split(":", 1)
        if not module_name or not class_name:
            raise ValueError(
                "Custom LLM backend must be in the form 'module.path:ClassName'"
            )
        module = importlib.import_module(module_name)
        return getattr(module, class_name), None

    canonical = _canonical_backend_name(backend)
    if canonical not in LLM_BACKEND_REGISTRY:
        raise ValueError(
            f"Unknown LLM backend '{backend}'. "
            "Use one of google/gemini/openai/anthropic or provide module.path:ClassName."
        )

    target = LLM_BACKEND_REGISTRY[canonical]
    module = importlib.import_module(target["module"])
    return getattr(module, target["class"]), canonical


def _default_model_for_backend(config: Any, canonical_backend: str | None) -> str | None:
    if not canonical_backend:
        return None
    target = LLM_BACKEND_REGISTRY.get(canonical_backend)
    if not target:
        return None
    config_attr = target.get("config_attr")
    if not config_attr or not hasattr(config, config_attr):
        return None
    provider_config = getattr(config, config_attr, None)
    if not provider_config:
        return None
    return getattr(provider_config, "default_model", None)


def _is_truthy(value: str | None, default: bool = True) -> bool:
    """Return True unless an env var is an explicit falsey token."""
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _parse_search_provider_chain(raw: str | None) -> list[str]:
    if not raw:
        return DEFAULT_SEARCH_PROVIDER_CHAIN.copy()
    tokens = [
        token.strip()
        for token in raw.split(",")
        if token and token.strip()
    ]
    return tokens or DEFAULT_SEARCH_PROVIDER_CHAIN.copy()


def _canonical_search_server_name(token: str) -> str:
    normalized = token.strip().lower()
    return SEARCH_PROVIDER_ALIASES.get(normalized, normalized)


def _search_provider_label(server_name: str) -> str:
    return SEARCH_PROVIDER_LABELS.get(server_name, server_name)


def _describe_search_chain(server_names: list[str]) -> str:
    if not server_names:
        return "your configured search tools"
    labels = [_search_provider_label(name) for name in server_names]
    if len(labels) == 1:
        return labels[0]
    return f"{labels[0]} (fallback: {', '.join(labels[1:])})"


def _describe_search_tool_usage(server_names: list[str]) -> str:
    hints = [
        SEARCH_PROVIDER_TOOL_HINTS[name]
        for name in server_names
        if name in SEARCH_PROVIDER_TOOL_HINTS
    ]
    if not hints:
        return (
            "- Use the available MCP search tools exactly as exposed in the tool menu "
            "(call them by their precise function names)."
        )
    return "\n".join(f"- {hint}" for hint in hints)


def _current_trace_logs() -> set[str]:
    pattern = os.path.join(TRACE_LOG_DIR, "financial_analyzer_traces-*.jsonl")
    return set(glob.glob(pattern))


def _collector_run_id_from_symlink() -> str | None:
    if not COLLECTOR_LOG_SYMLINK.exists() or not COLLECTOR_LOG_SYMLINK.is_symlink():
        return None
    try:
        target = os.readlink(COLLECTOR_LOG_SYMLINK)
    except OSError:
        return None
    target_name = Path(target).name
    match = COLLECTOR_LOG_PATTERN.fullmatch(target_name)
    if not match:
        return None
    return match.group("run_id")


def _resolve_run_id() -> str:
    from_env = (os.getenv("FINANCIAL_ANALYZER_RUN_ID") or "").strip()
    if from_env:
        return from_env
    collector_run_id = _collector_run_id_from_symlink()
    if collector_run_id:
        os.environ["FINANCIAL_ANALYZER_RUN_ID"] = collector_run_id
        return collector_run_id
    new_run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    os.environ["FINANCIAL_ANALYZER_RUN_ID"] = new_run_id
    return new_run_id


def _build_run_metadata(
    run_id: str,
    llm_backend: str,
    llm_canonical_backend: str | None,
    llm_model: str,
    search_requested: list[str],
    search_active: list[str],
    search_description: str,
    report_path: str,
) -> dict[str, Any]:
    timestamp = datetime.now(timezone.utc).isoformat()
    env_overrides = {
        key: os.getenv(key)
        for key in ENVIRONMENT_OVERRIDES
        if os.getenv(key) is not None
    }
    return {
        "metadata_version": METADATA_VERSION,
        "generated_at": timestamp,
        "run_id": run_id,
        "company": COMPANY_NAME,
        "report_path": report_path,
        "llm_backend": llm_backend,
        "llm_canonical_backend": llm_canonical_backend,
        "llm_model": llm_model,
        "search_providers_requested": search_requested,
        "search_providers_active": search_active,
        "search_provider_description": search_description,
        "sanity_mode": SANITY_MODE,
        "news_items_required": NEWS_ITEMS_REQUIRED,
        "research_max_refinements": RESEARCH_MAX_REFINEMENTS,
        "research_min_rating": RESEARCH_MIN_RATING.name,
        "orchestrator_max_iterations": ORCHESTRATOR_MAX_ITERATIONS,
        "cli_argv": sys.argv[1:],
        "env_overrides": env_overrides,
        "python_version": sys.version,
        "app_name": app.name,
    }


def _write_trace_metadata(
    logger,
    new_trace_logs: list[str],
    base_metadata: dict[str, Any],
    output_path: str,
):
    if not new_trace_logs:
        logger.warning("No new trace files detected; skipping metadata write.")
        return

    for trace_path in new_trace_logs:
        metadata = deepcopy(base_metadata)
        metadata["trace_log"] = os.path.relpath(trace_path, start=os.getcwd())
        metadata["trace_log_basename"] = os.path.basename(trace_path)
        metadata["report_path"] = output_path
        metadata_path = f"{trace_path}.metadata.json"
        os.makedirs(os.path.dirname(metadata_path), exist_ok=True)
        with open(metadata_path, "w", encoding="utf-8") as meta_file:
            json.dump(metadata, meta_file, indent=2, sort_keys=True)
        logger.info("Trace metadata written: %s", metadata_path)


def _configure_google_rate_limit(config, logger):
    """Apply optional rate limiting overrides from environment variables."""
    google_config = getattr(config, "google", None)
    if not google_config:
        return

    raw_requests = os.getenv("BENCHMARK_LLM_REQUESTS_PER_MIN") or os.getenv(
        "GOOGLE_RATE_LIMIT_REQUESTS"
    )
    raw_period = os.getenv("BENCHMARK_LLM_RATE_PERIOD") or os.getenv(
        "GOOGLE_RATE_LIMIT_PERIOD_SECONDS"
    )

    if raw_requests:
        try:
            google_config.rate_limit_requests = int(float(raw_requests))
        except ValueError:
            logger.warning("Invalid BENCHMARK_LLM_REQUESTS_PER_MIN value '%s'", raw_requests)

    if raw_period:
        try:
            google_config.rate_limit_period_seconds = float(raw_period)
        except ValueError:
            logger.warning("Invalid BENCHMARK_LLM_RATE_PERIOD value '%s'", raw_period)


SANITY_MODE = _is_truthy(os.getenv("FINANCIAL_ANALYZER_SANITY_MODE"), default=True)
NEWS_ITEMS_REQUIRED = 2 if SANITY_MODE else 5
RESEARCH_MAX_REFINEMENTS = 1 if SANITY_MODE else 3
RESEARCH_MIN_RATING = QualityRating.FAIR if SANITY_MODE else QualityRating.GOOD
ORCHESTRATOR_MAX_ITERATIONS = 3 if SANITY_MODE else 8

# Initialize app with tracing overrides applied before startup
APP_SETTINGS = get_settings()
_configure_otel_exporters(APP_SETTINGS, logger=None)
app = MCPApp(name="enhanced_stock_analyzer", human_input_callback=None, settings=APP_SETTINGS)


async def main():
    # Create output directory and set up file paths
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(TRACE_LOG_DIR, exist_ok=True)
    timestamp = _resolve_run_id()
    output_file = f"{COMPANY_NAME.lower().replace(' ', '_')}_report_{timestamp}.md"
    output_path = os.path.join(OUTPUT_DIR, output_file)
    existing_trace_logs = _current_trace_logs()

    if PRINT_ENV_ONLY:
        _print_seeded_env()
        return True

    metrics_interval_raw = os.getenv("FINANCIAL_ANALYZER_METRICS_INTERVAL", "").strip()
    metrics_interval_invalid = None
    metrics_interval_seconds = 15.0
    if metrics_interval_raw:
        try:
            metrics_interval_seconds = float(metrics_interval_raw)
        except ValueError:
            metrics_interval_invalid = metrics_interval_raw
    metrics_interval_seconds = max(1.0, metrics_interval_seconds)

    metrics_monitor_cm = SystemMetricsMonitor(
        service_name=app.name,
        output_dir="metrics",
        interval_seconds=metrics_interval_seconds,
        instrumentation_scope="financial-analyzer.system-metrics",
        environment=os.getenv("FINANCIAL_ANALYZER_ENV", "local"),
        run_id=timestamp,
    )

    async with app.run() as analyzer_app, metrics_monitor_cm as metrics_monitor:
        context = analyzer_app.context
        logger = analyzer_app.logger
        if metrics_interval_invalid:
            logger.warning(
                "Invalid FINANCIAL_ANALYZER_METRICS_INTERVAL '%s'; defaulting to 15 seconds.",
                metrics_interval_invalid,
            )
        metrics_monitor.logger = logger

        _configure_google_rate_limit(context.config, logger)
        if not _configure_otel_exporters(context.config, logger):
            return False

        for server in context.config.mcp.servers.values():
            if server.args:
                server.args = [os.path.expandvars(arg) for arg in server.args]

            requested_chain = _parse_search_provider_chain(REQUESTED_SEARCH_PROVIDERS)
            available_search_servers: list[str] = []
            unavailable_providers: list[str] = []
            for provider in requested_chain:
                server_name = _canonical_search_server_name(provider)
                if server_name in context.config.mcp.servers:
                    if server_name not in available_search_servers:
                        available_search_servers.append(server_name)
                else:
                    unavailable_providers.append(provider)

            if not available_search_servers:
                logger.error(
                    "No search MCP servers were configured for the requested providers (%s). "
                    "Please install/configure at least one search MCP server (Google, SerpAPI, Tavily, Bing).",
                    ", ".join(requested_chain),
                )
                return False

            if unavailable_providers:
                logger.warning(
                    "Skipping unavailable search providers: %s",
                    ", ".join(unavailable_providers),
                )

            logger.info(
                "Search providers enabled (in order): %s",
                ", ".join(_search_provider_label(name) for name in available_search_servers),
            )

            search_provider_description = _describe_search_chain(available_search_servers)
            search_tool_usage = _describe_search_tool_usage(available_search_servers)
            research_server_names = list(available_search_servers)
            if "fetch" not in research_server_names:
                research_server_names.append("fetch")

            run_metadata = None  # Filled in after the LLM backend loads successfully.

            canonical_backend: str | None = None
            try:
                llm_factory, canonical_backend = _load_llm_factory(LLM_BACKEND)
            except (ImportError, AttributeError, ValueError) as err:
                logger.error(f"Unable to load LLM backend '{LLM_BACKEND}': {err}")
                return False

            model_name = LLM_MODEL_OVERRIDE or _default_model_for_backend(
                context.config, canonical_backend
            )
            if not model_name:
                logger.error(
                    f"No model configured for backend '{LLM_BACKEND}'. "
                    "Use --llm-model to provide one."
                )
                return False

            logger.info(
                f"Using LLM backend '{LLM_BACKEND}' "
                f"(canonical: {canonical_backend or 'custom'}) with model '{model_name}'"
            )

            run_metadata = _build_run_metadata(
                run_id=timestamp,
                llm_backend=LLM_BACKEND,
                llm_canonical_backend=canonical_backend,
                llm_model=model_name,
                search_requested=requested_chain,
                search_active=available_search_servers,
                search_description=search_provider_description,
                report_path=output_path,
            )
            run_metadata["otel_exporter"] = (
                f"remote_otlp:{OTEL_REMOTE_ENDPOINT}" if OTEL_REMOTE_ENDPOINT else "local_file"
            )

            # Configure filesystem server to use current directory
            if "filesystem" in context.config.mcp.servers:
                context.config.mcp.servers["filesystem"].args.extend([os.getcwd()])
                logger.info("Filesystem server configured")
            else:
                logger.warning("Filesystem server not configured - report saving may fail")

            # --- SPECIALIZED AGENT DEFINITIONS ---

            scope_note = (
                "This run is a QUICK sanity check. Gather just enough factual data to "
                "prove the workflow works—prioritize accuracy over volume, stop once the "
                "required fields are filled."
                if SANITY_MODE
                else "Collect the full data pack required for a comprehensive briefing."
            )

            news_scope = (
                f"Provide {NEWS_ITEMS_REQUIRED} recent, well-sourced headlines."
            )

            # Data collection agent that gathers comprehensive financial information
            research_agent = Agent(
                name="data_collector",
                instruction=f"""You are a comprehensive financial data collector for {COMPANY_NAME}.
                {scope_note}

                Use {search_provider_description} together with fetch to gather the requested facts in the order listed. Stick to the available MCP functions exactly as named:
                {search_tool_usage}

                Prefer the most recent data and stop once each section has concrete numbers.

                **REQUIRED DATA TO COLLECT:**

                1. **Current Market Data**:
                   Search: "{COMPANY_NAME} stock price today current"
                   Search: "{COMPANY_NAME} trading volume market data"
                   Extract: Current price, daily change ($ and %), trading volume, 52-week range

                2. **Latest Earnings Information**:
                   Search: "{COMPANY_NAME} latest quarterly earnings results"
                   Search: "{COMPANY_NAME} earnings vs estimates beat miss"
                   Extract: EPS actual vs estimate, revenue actual vs estimate, beat/miss percentages

                3. **Recent Financial News**:
                   Search: "{COMPANY_NAME} financial news latest week"
                   Search: "{COMPANY_NAME} analyst ratings upgrade downgrade"
                   Extract: {news_scope}

                4. **Financial Metrics**:
                   Search: "{COMPANY_NAME} PE ratio market cap financial metrics"
                   Extract: P/E ratio, market cap, key financial ratios

                **OUTPUT FORMAT:**
                Organize your findings in these exact sections:

                ## CURRENT MARKET DATA
                - Stock Price: $XXX.XX (±X.XX, ±X.X%)
                - Trading Volume: X.X million (vs avg X.X million)
                - 52-Week Range: $XXX.XX - $XXX.XX
                - Market Cap: $XXX billion
                - Source: [URL and date]

                ## LATEST EARNINGS
                - EPS: $X.XX actual vs $X.XX estimate (beat/miss by X%)
                - Revenue: $XXX billion actual vs $XXX billion estimate (beat/miss by X%)
                - Year-over-Year Growth: X%
                - Quarter: QX YYYY
                - Source: [URL and date]

                ## RECENT NEWS (Last 7 Days)
                1. [Headline] - [Date] - [Source] - [Impact: Positive/Negative/Neutral]
                2. [Headline] - [Date] - [Source] - [Impact: Positive/Negative/Neutral]
                3. [Continue until you reach the required number of items]

                ## KEY FINANCIAL METRICS
                - P/E Ratio: XX.X
                - Market Cap: $XXX billion
                - [Other available metrics]
                - Source: [URL and date]

                **CRITICAL REQUIREMENTS:**
                - Use EXACT figures, not approximations
                - Include source URLs for verification
                - Note data timestamps/dates
                - If any section is missing data, explicitly state what couldn't be found
                """,
                server_names=research_server_names,
            )
            # research_agent.attach_llm(GoogleAugmentedLLM)

            # Quality control agent that enforces strict data standards
            evaluator_scope = (
                "Since this is a sanity-check run, treat the output as GOOD if the essential "
                "fields are present and sourced."
                if SANITY_MODE
                else "Hold the research to the full EXCELLENT standard."
            )

            research_evaluator = Agent(
                name="data_evaluator",
                instruction=f"""You are a strict financial data quality evaluator for {COMPANY_NAME} research.
                {evaluator_scope}

                **EVALUATION CRITERIA:**

                1. **COMPLETENESS CHECK** (Must have ALL of these):
                   ✓ Current stock price with exact dollar amount and percentage change
                   ✓ Latest quarterly EPS with actual vs estimate comparison
                   ✓ Latest quarterly revenue with actual vs estimate comparison
                   ✓ At least 3 recent financial news items with dates and sources
                   ✓ Key financial metrics (P/E ratio, market cap)
                   ✓ All data has proper source citations with URLs

                2. **ACCURACY CHECK**:
                   ✓ Numbers are specific (not "around" or "approximately")
                   ✓ Dates are recent and clearly stated
                   ✓ Sources are credible financial websites
                   ✓ No conflicting information without explanation

                3. **CURRENCY CHECK**:
                   ✓ Stock price data is from today or latest trading day
                   ✓ Earnings data is from most recent quarter
                   ✓ News items are from last 7 days (or most recent available)

                **RATING GUIDELINES:**

                - **EXCELLENT**: All criteria met perfectly, comprehensive data, multiple source verification
                - **GOOD**: All required data present, good quality sources, minor gaps acceptable
                - **FAIR**: Most required data present but missing some elements or has quality issues
                - **POOR**: Missing critical data (stock price, earnings, or major sources), unreliable sources

                **EVALUATION OUTPUT FORMAT:**

                COMPLETENESS: [EXCELLENT/GOOD/FAIR/POOR]
                - Stock price data: [Present/Missing] - [Details]
                - Earnings data: [Present/Missing] - [Details]
                - News coverage: [Present/Missing] - [Details]
                - Financial metrics: [Present/Missing] - [Details]
                - Source quality: [Excellent/Good/Fair/Poor] - [Details]

                ACCURACY: [EXCELLENT/GOOD/FAIR/POOR]
                - Data specificity: [Comments]
                - Source credibility: [Comments]
                - Data consistency: [Comments]

                CURRENCY: [EXCELLENT/GOOD/FAIR/POOR]
                - Stock data recency: [Comments]
                - Earnings recency: [Comments]
                - News recency: [Comments]

                OVERALL RATING: [EXCELLENT/GOOD/FAIR/POOR]

                **IMPROVEMENT FEEDBACK:**
                [Specific instructions for what needs to be improved, added, or fixed]
                [If rating is below GOOD, provide exact search queries needed]
                [List any missing data points that must be found]

                **CRITICAL RULE**: If ANY of these are missing, overall rating cannot exceed FAIR:
                - Exact current stock price with change
                - Latest quarterly EPS actual vs estimate
                - Latest quarterly revenue actual vs estimate
                - At least 2 credible news sources from recent period
                """,
                server_names=[],
            )
            # research_evaluator.attach_llm(GoogleAugmentedLLM)

            # Create the research quality control component
            research_quality_controller = EvaluatorOptimizerLLM(
                optimizer=research_agent,
                evaluator=research_evaluator,
                llm_factory=llm_factory,
                min_rating=RESEARCH_MIN_RATING,
                max_refinements=RESEARCH_MAX_REFINEMENTS,
            )

            # Financial analysis agent that provides investment insights
            analyst_scope = (
                "Keep this analysis brief (2 short paragraphs max) and highlight only the "
                "strongest bullish and bearish takeaways surfaced by the research."
                if SANITY_MODE
                else "Provide the full analysis outlined below."
            )

            analyst_agent = Agent(
                name="financial_analyst",
                instruction=f"""You are a senior financial analyst providing investment analysis for {COMPANY_NAME}.

                Based on the verified, high-quality data provided, create a comprehensive analysis. {analyst_scope}

                **1. STOCK PERFORMANCE ANALYSIS**
                - Analyze current price movement and trading patterns
                - Compare to historical performance and volatility
                - Assess volume trends and market sentiment indicators

                **2. EARNINGS ANALYSIS**
                - Evaluate earnings beat/miss significance
                - Analyze revenue growth trends and sustainability
                - Compare to guidance and analyst expectations
                - Identify key performance drivers

                **3. NEWS IMPACT ASSESSMENT**
                - Synthesize how recent news affects investment outlook
                - Identify market sentiment shifts
                - Highlight potential catalysts or risk factors

                **4. INVESTMENT THESIS DEVELOPMENT**

                **BULL CASE (Top 3 Strengths)**:
                1. [Strength with supporting data and metrics]
                2. [Strength with supporting data and metrics]
                3. [Strength with supporting data and metrics]

                **BEAR CASE (Top 3 Concerns)**:
                1. [Risk with supporting evidence and impact assessment]
                2. [Risk with supporting evidence and impact assessment]
                3. [Risk with supporting evidence and impact assessment]

                **5. VALUATION PERSPECTIVE**
                - Current valuation metrics analysis (P/E, etc.)
                - Historical valuation context
                - Fair value assessment based on fundamentals

                **6. RISK ASSESSMENT**
                - Company-specific operational risks
                - Market/sector risks and headwinds
                - Regulatory or competitive threats

                **OUTPUT REQUIREMENTS:**
                - Support all conclusions with specific data points
                - Use exact numbers and percentages from the research
                - Maintain analytical objectivity
                - Include confidence levels for key assessments
                - Cite data sources for major claims
                """,
                server_names=[],
            )
            # analyst_agent.attach_llm(GoogleAugmentedLLM)

            # Report generation agent that creates institutional-quality documents
            report_date = datetime.now().strftime("%B %d, %Y at %I:%M %p EST")

            if SANITY_MODE:
                report_instruction = f"""Create a concise, sanity-check markdown snapshot for {COMPANY_NAME}.

                **GOAL:** Confirm that the workflow surfaced real data. Use numbered lists or short paragraphs—keep the entire document under 400 words.

                # {COMPANY_NAME} Quick Financial Snapshot
                **Report Date:** {report_date}
                **Mode:** Sanity Check

                ## Market Pulse
                - Price + intraday change
                - Volume vs average
                - 52-week range position

                ## Earnings Pulse
                - Latest EPS actual vs estimate (state beat/miss)
                - Latest revenue actual vs estimate
                - YOY growth callout

                ## Headlines to Watch
                - Bullet the {NEWS_ITEMS_REQUIRED} news items with source + impact

                ## Key Metrics & Takeaways
                - P/E, market cap, or other notable ratios
                - 2 brief bullets on bullish/concern items

                Wrap up with a single-sentence overall assessment plus confidence level (High/Med/Low).
                Save the markdown to: {output_path}
                """
            else:
                report_instruction = f"""Create a comprehensive, institutional-quality financial report for {COMPANY_NAME}.

                **REPORT STRUCTURE** (Use exactly this format):

                # {COMPANY_NAME} - Comprehensive Financial Analysis
                **Report Date:** {report_date}
                **Analyst:** AI Financial Research Team

                ## Executive Summary
                **Current Price:** $XXX.XX (±$X.XX, ±X.X% today)
                **Market Cap:** $XXX.X billion
                **Investment Thesis:** [2-3 sentence summary of key investment outlook]
                **Recommendation:** [Overall assessment with confidence level: High/Medium/Low]

                ---

                ## Current Market Performance

                ### Trading Metrics
                - **Stock Price:** $XXX.XX (±$X.XX, ±X.X% today)
                - **Trading Volume:** X.X million shares (vs X.X million avg)
                - **52-Week Range:** $XXX.XX - $XXX.XX
                - **Current Position:** XX% of 52-week range
                - **Market Capitalization:** $XXX.X billion

                ### Technical Analysis
                [Analysis of price trends, volume patterns, momentum indicators]

                ---

                ## Financial Performance

                ### Latest Quarterly Results
                - **Earnings Per Share:** $X.XX actual vs $X.XX estimated (beat/miss by X.X%)
                - **Revenue:** $XXX.X billion actual vs $XXX.X billion estimated (beat/miss by X.X%)
                - **Year-over-Year Growth:** Revenue +/-X.X%, EPS +/-X.X%
                - **Quarter:** QX YYYY results

                ### Key Financial Metrics
                - **Price-to-Earnings Ratio:** XX.X
                - **Market Valuation:** [Analysis of current valuation vs historical/peers]

                ---

                ## Recent Developments

                ### Market-Moving News (Last 7 Days)
                [List 3-5 key news items with dates, sources, and impact analysis]

                ### Analyst Activity
                [Recent upgrades/downgrades, price target changes, consensus outlook]

                ---

                ## Investment Analysis

                ### Bull Case - Key Strengths
                1. **[Strength Title]:** [Detailed explanation with supporting data]
                2. **[Strength Title]:** [Detailed explanation with supporting data]
                3. **[Strength Title]:** [Detailed explanation with supporting data]

                ### Bear Case - Key Concerns
                1. **[Risk Title]:** [Detailed explanation with potential impact]
                2. **[Risk Title]:** [Detailed explanation with potential impact]
                3. **[Risk Title]:** [Detailed explanation with potential impact]

                ### Valuation Assessment
                [Current valuation analysis, fair value estimate, historical context]

                ---

                ## Risk Factors

                ### Company-Specific Risks
                - [Operational, competitive, management risks]

                ### Market & Sector Risks
                - [Economic, industry, regulatory risks]

                ---

                ## Investment Conclusion

                ### Summary Assessment
                [Balanced summary of key investment points]

                ### Overall Recommendation
                [Clear recommendation with rationale and confidence level]

                ### Price Target/Fair Value
                [If sufficient data available for valuation estimate]

                ---

                ## Data Sources & Methodology

                ### Sources Used
                [List all data sources with URLs and timestamps]

                ### Data Quality Notes
                [Any limitations, assumptions, or data quality considerations]

                ### Report Disclaimers
                *This report is for informational purposes only and should not be considered as personalized investment advice. Past performance does not guarantee future results. Please consult with a qualified financial advisor before making investment decisions.*

                ---

                **FORMATTING REQUIREMENTS:**
                - Use clean markdown formatting with proper headers
                - Include exact dollar amounts ($XXX.XX) and percentages (XX.X%)
                - Bold key metrics and important findings
                - Maintain professional, objective tone
                - Length: 1200-1800 words
                - Save to file: {output_path}

                **CRITICAL:** Ensure all data comes directly from the verified research. Do not add speculative information not supported by the collected data.
                """

            report_writer = Agent(
                name="report_writer",
                instruction=report_instruction,
                server_names=[],
            )
            # report_writer.attach_llm(GoogleAugmentedLLM)

            # --- CREATE THE ORCHESTRATOR ---
            run_mode = "sanity-check mode" if SANITY_MODE else "full-report mode"
            logger.info(
                f"Initializing stock analysis workflow for {COMPANY_NAME} ({run_mode})"
            )

            # Configure the orchestrator with our specialized agents
            pipeline_agents = [research_quality_controller]
            if not SANITY_MODE:
                pipeline_agents.append(analyst_agent)
            pipeline_agents.append(report_writer)

            orchestrator = Orchestrator(
                llm_factory=llm_factory,
                available_agents=pipeline_agents,
                plan_type="iterative" if SANITY_MODE else "full",
            )

            # Define the comprehensive analysis task
            if SANITY_MODE:
                task = f"""Create a quick sanity-check stock snapshot for {COMPANY_NAME}:

                1. Use 'research_quality_controller' once (with automatic evaluation) to gather:
                   - Today's stock price, change %, and volume vs average
                   - Latest EPS + revenue actual vs estimate
                   - {NEWS_ITEMS_REQUIRED} timely headlines with URLs
                   - Two valuation metrics (ex: P/E, market cap)

                2. Pass the verified notes to 'report_writer' so it produces a concise markdown file at "{output_path}" following the quick snapshot template.

                The goal is to produce trustworthy data with minimal latency—skip deep dives, but do include precise figures and citations.

                After you save the markdown report to "{output_path}", explicitly mark the plan complete (is_complete=true) so the workflow can stop."""
            else:
                task = f"""Create a high-quality stock analysis report for {COMPANY_NAME} by following these steps:

                1. Use the EvaluatorOptimizerLLM component (named 'research_quality_controller') to gather high-quality
                   financial data about {COMPANY_NAME}. This component will automatically evaluate
                   and improve the research until it reaches GOOD quality.

                   Ask for:
                   - Current stock price and recent movement
                   - Latest quarterly earnings results and performance vs expectations
                   - Recent news and developments

                2. Use the financial_analyst to analyze this research data and identify key insights.

                3. Use the report_writer to create a comprehensive stock report and save it to:
                   "{output_path}"

                The final report should be professional, fact-based, and include all relevant financial information.

                After you save the markdown report to "{output_path}", explicitly mark the plan complete (is_complete=true) so the workflow can stop."""

            # Execute the analysis workflow
            logger.info("Starting the stock analysis workflow")
            run_succeeded = False
            workflow_error: str | None = None
            try:
                orchestrator_params = RequestParams(
                    model=model_name,
                    maxTokens=2048 if SANITY_MODE else 4096,
                    max_iterations=ORCHESTRATOR_MAX_ITERATIONS,
                    temperature=0.4 if SANITY_MODE else 0.7,
                    use_history=False,
                )

                report_markdown = await orchestrator.generate_str(
                    message=task,
                    request_params=orchestrator_params,
                )

                # Persist report locally since filesystem tool is not used directly
                with open(output_path, "w", encoding="utf-8") as report_file:
                    report_file.write(report_markdown)

                if not report_markdown or not report_markdown.strip():
                    raise ValueError(
                        "Generated report was empty; treating workflow as a failure."
                    )
                # TODO: Add semantic validation to ensure the report meets benchmark criteria.

                logger.info(f"Report successfully generated: {output_path}")
                run_succeeded = True

            except Exception as e:
                workflow_error = str(e)
                logger.error(f"Error during workflow execution: {workflow_error}")
            finally:
                new_trace_logs = sorted(_current_trace_logs() - existing_trace_logs)
                if not new_trace_logs:
                    logger.warning("No new trace files detected; skipping metadata write.")
                elif not run_metadata:
                    logger.warning(
                        "Trace logs detected but base metadata was never initialized; skipping metadata write."
                    )
                else:
                    run_metadata["workflow_status"] = "ok" if run_succeeded else "failed"
                    run_metadata["workflow_completed"] = run_succeeded
                    if workflow_error:
                        run_metadata["workflow_error"] = workflow_error
                    else:
                        run_metadata.pop("workflow_error", None)
                    if INTERRUPTED_BY_USER:
                        run_metadata["interrupted"] = True
                    logger.info(
                        "Workflow finished with status '%s'; writing metadata sidecars.",
                        run_metadata["workflow_status"],
                    )
                    _write_trace_metadata(logger, new_trace_logs, run_metadata, output_path)

        return run_succeeded


if __name__ == "__main__":
    try:
        _install_interrupt_tracker()
        asyncio.run(main())
    finally:
        _remove_interrupt_tracker()
        _restore_seeded_env()
