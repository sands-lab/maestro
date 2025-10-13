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

from google.adk.agents.llm_agent import Agent

from .shared_libraries import constants

from .sub_agents.comparison.agent import comparison_root_agent
from .sub_agents.search_results.agent import search_results_agent
from .sub_agents.keyword_finding.agent import keyword_finding_agent
from google.adk.models.lite_llm import LiteLlm
import litellm
litellm._turn_on_debug()

from . import prompt

if constants.USE_LITELLM:
    llm = LiteLlm(
        model=constants.OLLAMA_MODEL,
        api_base=constants.OLLAMA_API_BASE)
else:
    llm = constants.MODEL

root_agent = Agent(
    model=llm,
    name=constants.AGENT_NAME,
    description=constants.DESCRIPTION,
    instruction=prompt.ROOT_PROMPT,
    sub_agents=[
        keyword_finding_agent,
        search_results_agent,
        comparison_root_agent,
    ],
)
