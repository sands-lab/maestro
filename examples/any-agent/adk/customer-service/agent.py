from any_agent import AnyAgent, AgentConfig

from callbacks import AgentCallback, get_default_callbacks

from tools.tools import (
    access_cart_information,
    approve_discount,
    check_product_availability,
    generate_qr_code,
    get_available_planting_times,
    get_product_recommendations,
    modify_cart,
    schedule_planting_service,
    send_call_companion_link,
    send_care_instructions,
    sync_ask_for_approval,
    update_salesforce_crm,
)

from prompts import GLOBAL_INSTRUCTION, INSTRUCTION

import logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

agent = AnyAgent.create(
    "google",
    AgentConfig(
        model_id="openai:gpt-5-mini",
        instructions=GLOBAL_INSTRUCTION + '\n\n' + INSTRUCTION,
        tools=[
            access_cart_information,
            approve_discount,
            check_product_availability,
            generate_qr_code,
            get_available_planting_times,
            get_product_recommendations,
            modify_cart,
            schedule_planting_service,
            send_call_companion_link,
            send_care_instructions,
            sync_ask_for_approval,
            update_salesforce_crm,
        ],
        callbacks=[
            AgentCallback(),
            *get_default_callbacks(),
        ],
    ),
)

if __name__ == "__main__":
    try:
        with open('test_query.json', 'r') as f:
            import json
            query = f.read()
            query = json.loads(query)
        for q in query:
            agent.run(q['query'])
    except RuntimeError as e:
        print(f"\n✅ Success! The agent was stopped: {e}")