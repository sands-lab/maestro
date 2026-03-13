# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Defines Brand Search Optimization Agent"""

from any_agent import AnyAgent, AgentConfig

from sub_agents.comparison.agent import comparison_root_agent_func
from sub_agents.search_results.agent import search_results_agent_func
from sub_agents.keyword_finding.agent import keyword_finding_agent_func

import prompt


import asyncio
import json

async def create_root_agent():
    """Create and return the root agent instance."""
    root_agent = await AnyAgent.create_async(
        agent_framework="google",
        agent_config=AgentConfig(
            model_id = "openai:gpt-5-mini",
            instructions = prompt.ROOT_PROMPT,
            tools=[
                keyword_finding_agent_func,
                search_results_agent_func,
                comparison_root_agent_func,
            ],
        ),
    )
    return root_agent

async def main():
    root_agent = await create_root_agent()
    
    try:
        with open('user_statements.json', 'r') as f:
            user_statements = json.load(f)
            for s in user_statements:
                # Ensure we await the async execution
                result = await root_agent.run_async(s)
                
    except FileNotFoundError:
        print("user_statements.json not found. Running default query.")
        await root_agent.run_async("What is the best brand for running shoes?")

if __name__ == "__main__":
    asyncio.run(main())