#!/usr/bin/env bash
set -euo pipefail

sudo docker build . -t 10.140.0.2:5001/content_writer_agent:latest \
  --build-arg DOCKER_REGISTRY=localhost:5001 \
  --build-arg VERSION=latest \
  --push \
  --no-cache
