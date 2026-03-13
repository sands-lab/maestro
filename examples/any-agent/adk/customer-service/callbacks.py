import logging

from any_agent.callbacks import Callback, Context, get_default_callbacks
from google.adk.sessions import State
from jsonschema import ValidationError
from entities.customer import Customer
import time

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

RATE_LIMIT_SECS = 60
RPM_QUOTA = 10
MAX_DISCOUNT_RATE = 10

def lowercase_value(value):
    """Make dictionary lowercase"""
    if isinstance(value, dict):
        return (dict(k, lowercase_value(v)) for k, v in value.items())
    elif isinstance(value, str):
        return value.lower()
    elif isinstance(value, (list, set, tuple)):
        tp = type(value)
        return tp(lowercase_value(i) for i in value)
    else:
        return value

def validate_customer_id(
    customer_id: str, session_state: State
) -> tuple[bool, str]:
    if "customer_profile" not in session_state:
        return False, "No customer profile selected. Please select a profile."

    try:
        c = Customer.model_validate_json(session_state["customer_profile"])
        if customer_id == c.customer_id:
            return True, None
        else:
            return (
                False,
                "You cannot use the tool with customer_id "
                + customer_id
                + ", only for "
                + c.customer_id
                + ".",
            )
    except ValidationError:
        return (
            False,
            "Customer profile couldn't be parsed. Please reload the customer data. ",
        )


class AgentCallback(Callback):
    def before_agent_invocation(self, context: Context, *args, **kwargs) -> Context:
        logger.debug("Before agent invocation")
        if "customer_profile" not in context.shared:
            context.shared["customer_profile"] = Customer.get_customer(
                "123"
            ).to_json()
        
        return context

    def before_llm_call(self, context: Context, *args, **kwargs) -> Context:
        logger.debug("Before LLM call")
        
        llm_request = kwargs.get("llm_request")
        callback_context = kwargs.get("callback_context")
        
        for content in llm_request.contents:
            for part in content.parts:
                if part.text == "":
                    part.text = " "

        now = time.time()
        if "timer_start" not in callback_context.state:
            callback_context.state["timer_start"] = now
            callback_context.state["request_count"] = 1
            logger.debug(
                "rate_limit_callback [timestamp: %i, "
                "req_count: 1, elapsed_secs: 0]",
                now,
            )
            return

        request_count = callback_context.state["request_count"] + 1
        elapsed_secs = now - callback_context.state["timer_start"]
        logger.debug(
            "rate_limit_callback [timestamp: %i, request_count: %i,"
            " elapsed_secs: %i]",
            now,
            request_count,
            elapsed_secs,
        )

        if request_count > RPM_QUOTA:
            delay = RATE_LIMIT_SECS - elapsed_secs + 1
            if delay > 0:
                logger.debug("Sleeping for %i seconds", delay)
                time.sleep(delay)
            callback_context.state["timer_start"] = now
            callback_context.state["request_count"] = 1
        else:
            callback_context.state["request_count"] = request_count
            
        return context


    def before_tool_execution(self, context: Context, *args, **kwargs) -> Context:
        logger.debug("Before tool execution")

        lowercase_value(args)

        tool = kwargs.get("tool")
        tool_context = kwargs.get("tool_context")
        args = kwargs.get("args")

        if "customer_id" in args:
            valid, err = validate_customer_id(
                args["customer_id"], tool_context.state
            )
        if not valid:
            return err

        if tool.name == "sync_ask_for_approval":
            amount = args.get("value", None)
            if amount <= MAX_DISCOUNT_RATE:
                return {
                    "status": "approved",
                    "message": "You can approve this discount; no manager needed.",
                }

        if tool.name == "modify_cart":
            if (
                args.get("items_added") is True
                and args.get("items_removed") is True
            ):
                return {"result": "I have added and removed the requested items."}
        return context

    def after_tool_execution(self, context: Context, *args, **kwargs) -> Context:
        logger.debug("After tool execution")

        tool = kwargs.get("tool")
        tool_response = kwargs.get("tool_response")
        
        if tool.name == "sync_ask_for_approval":
            if tool_response["status"] == "approved":
                logger.debug("Applying discount to the cart")

        if tool.name == "approve_discount":
            if tool_response["status"] == "ok":
                logger.debug("Applying discount to the cart")

        return context
