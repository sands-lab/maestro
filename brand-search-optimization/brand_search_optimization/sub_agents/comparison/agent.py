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

from google.adk.agents.llm_agent import Agent
from google.adk.models.lite_llm import LiteLlm
import litellm
litellm._turn_on_debug()

from ...shared_libraries import constants
from . import prompt


if constants.USE_LITELLM:
    llm = LiteLlm(
        model=constants.OLLAMA_MODEL,
        api_base=constants.OLLAMA_API_BASE)
else:
    llm = constants.MODEL

comparison_generator_agent = Agent(
    model=llm,
    name="comparison_generator_agent",
    description="A helpful agent to generate comparison.",
    instruction=prompt.COMPARISON_AGENT_PROMPT,
)

comparsion_critic_agent = Agent(
    model=llm,
    name="comparison_critic_agent",
    description="A helpful agent to critique comparison.",
    instruction=prompt.COMPARISON_CRITIC_AGENT_PROMPT,
)

comparison_root_agent = Agent(
    model=llm,
    name="comparison_root_agent",
    description="A helpful agent to compare titles",
    instruction=prompt.COMPARISON_ROOT_AGENT_PROMPT,
    sub_agents=[comparison_generator_agent, comparsion_critic_agent],
)
