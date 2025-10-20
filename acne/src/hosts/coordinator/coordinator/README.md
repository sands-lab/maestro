# Multi-Language Agent Collaboration and Interoperability

> *⚠️ DISCLAIMER: THIS DEMO IS INTENDED FOR DEMONSTRATION PURPOSES ONLY. IT IS NOT INTENDED FOR USE IN A PRODUCTION ENVIRONMENT.*
> *⚠️ Important: A2A is a work in progress (WIP) thus, in the near future there might be changes that are different from what demonstrated here.*

This sample highlights how to use the Agent2Agent (A2A) protocol for multi-agent collaboration where multiple
agents, each written in a different programming language, seamlessly work together to accomplish a goal.

The sample also highlights the interoperability made possible by the A2A protocol, demonstrating how an agent
can be easily swapped out for an equivalent agent written in a different programming language. This also permits step-by-step migration of complex systems.

## Architecture

The application features a content creation pipeline with a host agent that routes requests to the appropriate specialized agent:

* **Host Agent** *(Python, Google ADK, A2A Python SDK)*: Acts as the central orchestrator for content creation, dynamically determining which agent to send a request to based on the task at hand.
* **Content Planner Agent** *(Python, Google ADK, A2A Python SDK)*: Receives a high-level description of the content that's needed and creates a detailed content outline.
* **Content Writer Agent** *(Java, Quarkus LangChain4j, A2A Java SDK)*: Generates an engaging piece of content using a content outline.
* **Content Editor Agent** *(TypeScript, Genkit, A2A JS SDK)*: Proof-reads and polishes content.

## App UI

![architecture](assets/UI.png)

## Setup and Deployment

### Prerequisites

Before running the application locally, ensure you have the following installed:

1. **uv:** The Python package management tool used in this project. Follow the installation guide: [https://docs.astral.sh/uv/getting-started/installation/](https://docs.astral.sh/uv/getting-started/installation/)
2. **python 3.13** Python 3.13 is required to run a2a-sdk

## Building Docker

```bash
docker build -t a2a/content_creation .
```
