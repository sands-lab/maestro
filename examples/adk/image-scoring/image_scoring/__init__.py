"""Image Scoring Agent for generating and scoring images based on input text."""

import os

import google.auth
from dotenv import load_dotenv

load_dotenv()

_, project_id = google.auth.default()
os.environ.setdefault("GOOGLE_CLOUD_PROJECT", project_id)
os.environ["GOOGLE_CLOUD_LOCATION"] = "global"
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "True")

# Initialize OpenTelemetry tracing and metrics
from .telemetry_setup import setup_tracing, setup_metrics

setup_tracing(service_name="image-scoring")
setup_metrics(service_name="image-scoring")

from . import agent
