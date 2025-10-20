ARG DOCKER_REGISTRY=ghcr.io
ARG VERSION=latest
FROM $DOCKER_REGISTRY/kagent-dev/kagent/kagent-adk:$VERSION
LABEL org.opencontainers.image.authors="Yixi Chen <yixi.chen@kaust.edu.sa>>"

ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8

ARG DEBIAN_FRONTEND="noninteractive"

EXPOSE 10002

WORKDIR /app

COPY . .

# Install dependencies if pyproject exists, otherwise skip
RUN uv sync --no-cache-dir && \
    rm -rf /tmp/* /var/lib/apt/lists/* /var/tmp/*

COPY  content_writer/ content_writer/
CMD ["content_writer"]
