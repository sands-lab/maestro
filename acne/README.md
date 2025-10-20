# acne

from [samples/python/hosts/content_creation](https://github.com/a2aproject/a2a-samples/tree/883c8906a60cb48e43599db0480d2217bd02c320/samples/python/hosts/content_creation)

Research sandbox for agent communication and networking experiments on a container-based emulator.

**ACNE: Agent Communication Network Experiments** is a research sandbox for exploring networking challenges in distributed AI systems. This repository provides a containerized experimental setup using the Kathara network emulator to study topics such as decentralized load balancing, routing, and KV-cache distribution for heterogeneous AI agents.

# About this branch

This branch provides a kagent deployment of the acne example.

How to run:
1. Install [kagent](https://kagent.dev/docs/kagent/introduction/installation).
2. In `src/agents/content_editor` and `src/agents/content_planner` and `src/agents/content_writer` and `hosts/coordinator` directories, run `./build_docker.sh` to build the docker image, then run `kubectl apply -f agent.yaml` to deploy the agents.
3. In `src/hosts/coordinator` directory, run `./port_forwarding.sh` to start port forwarding.
4. Two ways to access the coordinator:
   - using [A2A host CLI](https://kagent.dev/docs/kagent/examples/a2a-agents#a2a-host-cli)(recommended):
   ```
   git clone https://github.com/a2aproject/a2a-samples.git
   ln -s start_acne.sh ~/a2a-samples/samples/python/hosts/cli/start_acne.sh
   cd ~/a2a-samples/samples/python/hosts/cli
   ./start_acne.sh
   ```
   - using kagent invoke (maybe timeout because of the long duration of conversation between agents):
   ```
   kagent invoke --agent coordinator-agent --task "Who are you"
   ```