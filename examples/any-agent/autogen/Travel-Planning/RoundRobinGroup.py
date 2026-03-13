import json

class RoundRobinGroup:
    def __init__(self, agents, termination_condition):
        self.agents = agents
        self.index = 0
        self.context = []
        self.termination_condition = termination_condition

    
    def get_next_agent(self):
        agent = self.agents[self.index]
        self.index = (self.index + 1) % len(self.agents)
        return agent

    async def run(self, task):
        self.context.append(
            {
                "role": "user",
                "content": task
            }
        )
        while True:
            agent = self.get_next_agent()
            agent_trace = await agent.run_async(json.dumps(self.context))
            output = str(agent_trace.final_output)
            self.context.append(
                {
                    "role": "assistant",
                    "content": output
                }
            )
            if self.termination_condition in output[len(output)-20:]:
                break
        return output
