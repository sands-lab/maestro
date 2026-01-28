ARG DOCKER_REGISTRY=ghcr.io
ARG VERSION=latest
FROM $DOCKER_REGISTRY/kagent-dev/kagent/kagent-adk:$VERSION
LABEL org.opencontainers.image.authors="Yixi Chen <yixi.chen@kaust.edu.sa>>"

ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8

ARG DEBIAN_FRONTEND="noninteractive"
# RUN apt update

# Install uv and the Python packages.
# RUN python3 -m pip install --no-cache-dir --upgrade --break-system-packages uv

# EXPOSE 10003

WORKDIR /app

COPY . .

# Use the uv lock file in the project's working directory
RUN uv sync --no-cache-dir && \
    rm -rf /tmp/* /var/lib/apt/lists/* /var/tmp/*

COPY  content_editor/ content_editor/
CMD ["content_editor"]
