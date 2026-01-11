# Autonomously complete a coding task:
import asyncio
import os
import sys

import logging

# Get the autogen token_count_utils logger
logger = logging.getLogger("autogen.token_count_utils")

# Define a filter
class ModelMismatchFilter(logging.Filter):
    def filter(self, record):
        # Filter out messages containing both "Model" and "not found"
        return not ("Model" in record.getMessage() and "not found" in record.getMessage())

# Add the filter to the logger
logger.addFilter(ModelMismatchFilter())

from autogen_agentchat.ui import Console
from autogen_ext.code_executors.docker import DockerCommandLineCodeExecutor
from autogen_ext.models.openai import OpenAIChatCompletionClient
from autogen_ext.teams.magentic_one import MagenticOne

# Get the absolute path of the current script
current_dir = os.path.dirname(os.path.abspath(__file__))
# Get the parent directory (autogen-examples)
parent_dir = os.path.dirname(current_dir)

# Add the parent directory to sys.path
sys.path.append(parent_dir)

# Import our custom AutoGen exporters
from autogen_exporters import setup_autogen_metrics, setup_autogen_tracing
from opentelemetry import metrics, trace
from opentelemetry.instrumentation.openai import OpenAIInstrumentor
from opentelemetry.instrumentation.system_metrics import SystemMetricsInstrumentor

# ==========================================================
# Configure AutoGen-compatible exporters (JSON files and SigNoz)
# ==========================================================

# Set up AutoGen-compatible tracing; export to local JSON and SigNoz
tracer_provider = setup_autogen_tracing(
    service_name="magentic-one-coding-task",
    enable_signoz=True,
    signoz_endpoint="http://localhost:4317",
)

# Set up AutoGen-compatible metrics; export to local JSON and SigNoz
meter_provider = setup_autogen_metrics(
    service_name="magentic-one-coding-task",
    enable_system_metrics=True,
    enable_signoz=True,
    signoz_endpoint="http://localhost:4317",
)

# Auto-collect CPU usage, memory usage, network I/O, etc.
SystemMetricsInstrumentor().instrument()

# Auto-collect OpenAI request token usage and latency
OpenAIInstrumentor().instrument()

# Get tracer and meter for manual instrumentation
tracer = trace.get_tracer("magentic-one-custom-tracer")
meter = metrics.get_meter("magentic-one-custom-metrics")

# Define a histogram to track message size distribution
message_size_histogram = meter.create_histogram(
    name="autogen.agent.message_size",
    description="Size of messages processed by AutoGen agents",
    unit="bytes",
)

# Define a counter to track task execution
task_counter = meter.create_counter(
    name="magentic_one.tasks.count",
    description="Number of tasks executed by Magentic One",
)

# Define a histogram to track task duration
task_duration_histogram = meter.create_histogram(
    name="magentic_one.task.duration",
    description="Duration of task execution",
    unit="seconds",
)


async def example_usage():
    with tracer.start_as_current_span("magentic_one_coding_task") as span:
        # Record task start
        task_counter.add(1, {"task_type": "coding_task"})

        # Set span attributes
        span.set_attribute("task.type", "coding_task")
        span.set_attribute("model.name", "gpt-5")
        span.set_attribute("executor.type", "docker")

        client = OpenAIChatCompletionClient(model="gpt-5-mini")

        async with DockerCommandLineCodeExecutor(
            image="my-docker-runner:latest"
        ) as code_executor:
            with tracer.start_as_current_span("initialize_magentic_one"):
                m1 = MagenticOne(
                    client=client, code_executor=code_executor
                )  # Uses DockerCommandLineCodeExecutor by default

            task = """The attached txt contains a Python script. Run the Python code against an array of strings, listed below. The output of the Python script will be a URL containing C++ source code. Compile and run this C++ code against the array [35, 12, 8, 99, 21, 5] and return the sum of the third and fifth integers in the sorted list.
arr = ['_alg', 'ghi', 'C++', 'jkl', 'tps', '/Q', 'pqr', 'stu', ':', '//', 'rose', 'vwx', 'yz1', '234', 'tta', '567', '890', 'cod', 'e.', 'or', 'g/', 'wiki', '/', 'ing', 'sort', 'abc' , 'or', 'it', 'hms', 'mno' , 'uic', 'ksort', '#', 'ht' ]
"""

            # Record task input size
            task_size = len(task.encode('utf-8'))
            task_size = len(task.encode("utf-8"))
            message_size_histogram.record(task_size, {"message_type": "task_input"})
            span.set_attribute("task.input_size", task_size)

            with tracer.start_as_current_span("execute_task") as task_span:
                task_span.set_attribute("task.content_length", len(task))
                result = await Console(m1.run_stream(task=task))

                # Record result size
                if result:
                    result_size = len(str(result).encode("utf-8"))
                    message_size_histogram.record(
                        result_size, {"message_type": "task_output"}
                    )

                print(result)

        await client.close()


if __name__ == "__main__":
    asyncio.run(example_usage())
