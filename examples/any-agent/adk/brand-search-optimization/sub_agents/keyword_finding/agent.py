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

"""Defines keyword finding agent."""

from any_agent import AnyAgent, AgentConfig
from tools import bq_connector
from sub_agents.keyword_finding import prompt

async def keyword_finding_agent_func(user_task: str) -> str:
    keyword_finding_agent = await AnyAgent.create_async(
        agent_framework="google",
        agent_config=AgentConfig(
            model_id = "openai:gpt-5-mini",
            instructions = prompt.KEYWORD_FINDING_AGENT_PROMPT,
            tools=[
                bq_connector.get_product_details_for_brand,
            ],
        ),
    )
    agent_trace = await keyword_finding_agent.run_async(user_task)
    return str(agent_trace.final_output)

keyword_finding_agent_func.__doc__ = prompt.KEYWORD_FINDING_AGENT_PROMPT