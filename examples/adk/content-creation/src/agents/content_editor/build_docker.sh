sudo docker build . -t 10.140.0.2:5001/content_editor_agent:latest \
  --build-arg DOCKER_REGISTRY=localhost:5001 \
  --build-arg VERSION=latest \
  --push \
  --no-cache
