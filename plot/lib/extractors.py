"""Data extraction utilities for OpenTelemetry trace and metrics data."""

import json
import re
from collections import defaultdict
from typing import Dict, List, Tuple, Optional


def _safe_lower(value: object) -> str:
    if isinstance(value, str):
        return value.lower()
    return ""


def _safe_int(value: object) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return 0


def extract_token_consumption(traces: List[Dict]) -> Tuple[Dict[str, int], Dict[str, Dict[str, int]]]:
    """Extract token consumption from traces.

    Returns:
        (total_tokens, per_agent_tokens)
        total_tokens: {'prompt': int, 'completion': int, 'total': int}
        per_agent_tokens: {agent_name: {'prompt': int, 'completion': int, 'total': int}}
    """
    # Build span dictionary for parent lookup
    span_dict = {span['span_id']: span for span in traces}

    total_tokens = {'prompt': 0, 'completion': 0, 'total': 0}
    per_agent_tokens = defaultdict(lambda: {'prompt': 0, 'completion': 0, 'total': 0})

    for span in traces:
        span_name = _safe_lower(span.get('name'))
        attrs = span.get('attributes', {})

        # Check if this is an LLM call span
        # Priority 1: Use gen_ai.operation.name if available (per template)
        operation_name = _safe_lower(attrs.get('gen_ai.operation.name'))
        is_llm_span = operation_name == 'call_llm'

        # Priority 2: Fallback to span name pattern matching (for compatibility)
        if not is_llm_span:
            is_llm_span = (
                'call_llm' in span_name or
                'openai.chat' in span_name or
                ('generate' in span_name and 'llm' in span_name) or
                attrs.get('gen_ai.usage.input_tokens') or
                attrs.get('gen_ai.usage.output_tokens')
            )

        if is_llm_span:
            # Use prompt_token_count and candidates_token_count if available
            llm_response = attrs.get('gcp.vertex.agent.llm_response', '')
            prompt_tokens = attrs.get('gen_ai.usage.input_tokens', 0) or 0
            completion_tokens = attrs.get('gen_ai.usage.output_tokens', 0) or 0

            # Try to extract from llm_response if available
            if llm_response:
                try:
                    response_data = json.loads(llm_response)
                    usage_metadata = response_data.get('usage_metadata', {})
                    if usage_metadata:
                        prompt_tokens = usage_metadata.get('prompt_token_count', prompt_tokens)
                        completion_tokens = usage_metadata.get('candidates_token_count', completion_tokens)
                except:
                    pass

            if prompt_tokens or completion_tokens:
                # Find agent name from span attributes or parent spans (search up the tree)
                agent_name = 'unknown'
                # First try to get from current span attributes
                agent_name = attrs.get('gen_ai.agent.name') or span.get('agent_name') or 'unknown'

                # If not found, search up parent chain
                if agent_name == 'unknown':
                    current = span
                    max_levels = 5
                    for _ in range(max_levels):
                        parent_id = current.get('parent_span_id')
                        if not parent_id or parent_id not in span_dict:
                            break

                        parent_span = span_dict[parent_id]
                        parent_name = _safe_lower(parent_span.get('name'))
                        parent_attrs = parent_span.get('attributes', {})

                        # Check if parent has agent name
                        parent_agent_name = parent_attrs.get('gen_ai.agent.name') or parent_span.get('agent_name')
                        if parent_agent_name:
                            agent_name = parent_agent_name
                            break

                        # Check if parent is an agent span (OpenAIAugmentedLLM, Agent, etc.)
                        if ('invoke_agent' in parent_name or
                            'augmentedllm' in parent_name or
                            ('agent' in parent_name and 'generate' in parent_name)):
                            # Try to extract from parent name or attributes
                            if parent_agent_name:
                                agent_name = parent_agent_name
                                break

                        current = parent_span

                total_tokens['prompt'] += prompt_tokens
                total_tokens['completion'] += completion_tokens
                total_tokens['total'] += (prompt_tokens + completion_tokens)

                per_agent_tokens[agent_name]['prompt'] += prompt_tokens
                per_agent_tokens[agent_name]['completion'] += completion_tokens
                per_agent_tokens[agent_name]['total'] += (prompt_tokens + completion_tokens)

    return total_tokens, dict(per_agent_tokens)


def extract_delay_breakdown(traces: List[Dict]) -> Tuple[Dict[str, List[float]], Dict[str, List[float]], Dict[Tuple[str, str], List[float]], Dict[str, List[float]]]:
    """Extract delay breakdown from traces.

    Returns:
        (component_delays, per_agent_delays, inter_agent_delays, agent_llm_delays)
        component_delays: {
            'agent_llm_delay': [durations in ms],
            'inter_agent_delay': [durations in ms],
            'agent_processing_delay': [durations in ms],
            'e2e_delay': [durations in ms]
        }
        per_agent_delays: {agent_name: [durations in ms]}
        inter_agent_delays: {(source, target): [durations in ms]}
        agent_llm_delays: {agent_name: [durations in ms]}
    """
    delays = {
        'agent_llm_delay': [],
        'inter_agent_delay': [],
        'agent_processing_delay': [],
        'e2e_delay': []
    }

    # Build span tree
    span_dict = {span['span_id']: span for span in traces}
    root_spans = [span for span in traces if span.get('parent_span_id') is None]

    per_agent_delays = defaultdict(list)  # {agent_name: [durations]}
    inter_agent_delays = defaultdict(list)  # {(source, target): [durations]}
    agent_llm_delays = defaultdict(list)  # {agent_name: [durations]}
    agent_llm_delays_detail = defaultdict(list)  # {agent_name: [durations]} for detailed breakdown

    # Agent-LLM delay: LLM call spans with agent attribution
    # Support multiple span name patterns: call_llm, openai.chat, generate, etc.
    for span in traces:
        span_name = _safe_lower(span.get('name'))
        attrs = span.get('attributes', {})

        # Check if this is an LLM call span
        # Priority 1: Use gen_ai.operation.name if available (per template)
        operation_name = _safe_lower(attrs.get('gen_ai.operation.name'))
        is_llm_span = operation_name == 'call_llm'

        # Priority 2: Fallback to span name pattern matching (for compatibility)
        if not is_llm_span:
            is_llm_span = (
                'call_llm' in span_name or
                'openai.chat' in span_name or
                ('generate' in span_name and 'llm' in span_name) or
                attrs.get('gen_ai.usage.input_tokens') or
                attrs.get('gen_ai.usage.output_tokens')
            )

        if is_llm_span:
            duration_ns = _safe_int(span.get('duration_ns'))
            duration_ms = duration_ns / 1e6
            delays['agent_llm_delay'].append(duration_ms)

            # Find agent name from span attributes or parent span
            agent_name = 'unknown'
            # First try to get from current span attributes
            agent_name = attrs.get('gen_ai.agent.name') or span.get('agent_name', 'unknown')

            # If not found, try parent span
            if agent_name == 'unknown':
                parent_id = span.get('parent_span_id')
                if parent_id and parent_id in span_dict:
                    parent_span = span_dict[parent_id]
                    parent_name = _safe_lower(parent_span.get('name'))
                    # Support multiple parent span patterns
                    if ('invoke_agent' in parent_name or
                        'agent' in parent_name or
                        'augmentedllm' in parent_name):
                        parent_attrs = parent_span.get('attributes', {})
                        agent_name = parent_attrs.get('gen_ai.agent.name') or parent_span.get('agent_name', 'unknown')
            agent_llm_delays[agent_name].append(duration_ms)
            agent_llm_delays_detail[agent_name].append(duration_ms)

    # Inter-agent delay: execute_tool spans with source/target (AgentTool) or invoke_agent spans with parent invoke_agent (SequentialAgent/LoopAgent)
    for span in traces:
        span_name = _safe_lower(span.get('name'))
        comm = span.get('communication', {})
        attrs = span.get('attributes', {})
        is_agent_comm = False
        source_agent = 'unknown'
        target_agent = 'unknown'

        # Method 1: Check execute_tool spans (for AgentTool in marketing-agency or send_message in distributed systems)
        if 'execute_tool' in span_name:
            tool_type = attrs.get('gen_ai.tool.type', '')

            # Check if it's agent communication: either marked as such, or is AgentTool type
            is_agent_comm = comm.get('is_agent_communication', False) or tool_type == 'AgentTool'

            # Also check if it's a send_message tool (common in distributed systems)
            if not is_agent_comm:
                tool_name = _safe_lower(attrs.get('gen_ai.tool.name'))
                if 'message' in tool_name or 'agent' in tool_name:
                    is_agent_comm = True

            if is_agent_comm:
                # Source: traverse up to find invoke_agent span
                current_id = span.get('parent_span_id')
                depth = 0
                while current_id and depth < 10:
                    if current_id in span_dict:
                        parent_span = span_dict[current_id]
                        if 'invoke_agent' in _safe_lower(parent_span.get('name')):
                            parent_attrs = parent_span.get('attributes', {})
                            source_agent = parent_attrs.get('gen_ai.agent.name') or parent_span.get('agent_name', 'unknown')
                            break
                        current_id = parent_span.get('parent_span_id')
                        depth += 1
                    else:
                        break

                # Target: from span name or attributes
                target_agent = attrs.get('gen_ai.agent.name') or attrs.get('adk.agent.name') or span.get('agent_name', 'unknown')

                # For send_message tool in distributed systems, extract agent_name from tool_call_args
                if target_agent == 'unknown' and 'send_message' in span_name:
                    tool_call_args = attrs.get('gcp.vertex.agent.tool_call_args', '')
                    if tool_call_args:
                        try:
                            import json
                            args = json.loads(tool_call_args) if isinstance(tool_call_args, str) else tool_call_args
                            if isinstance(args, dict) and 'agent_name' in args:
                                target_agent = args['agent_name']
                        except:
                            pass

                # Fallback: try to extract from span name if not found in attributes
                if target_agent == 'unknown':
                    match = re.search(r'(?:execute_tool|invoke_agent)\s+(\w+)', span.get('name', ''), re.IGNORECASE)
                    if match:
                        target_agent = match.group(1)

        # Method 2: Check invoke_agent spans with parent invoke_agent (for SequentialAgent/LoopAgent in image-scoring)
        # NOTE: For monolithic systems with SequentialAgent/LoopAgent, invoke_agent spans represent agent-to-agent calls.
        # The duration includes the entire sub-agent execution (LLM calls, tool calls, etc.), similar to AgentTool in marketing-agency.
        # Both AgentTool (execute_tool) and SequentialAgent/LoopAgent (invoke_agent) represent inter-agent communication,
        # so we should count the duration as inter-agent delay for both cases.
        elif 'invoke_agent' in span_name:
            # Check if parent span is also an invoke_agent (indicating agent-to-agent call)
            parent_id = span.get('parent_span_id')
            if parent_id and parent_id in span_dict:
                parent_span = span_dict[parent_id]
                parent_name = _safe_lower(parent_span.get('name'))
                if 'invoke_agent' in parent_name:
                    # This is an agent-to-agent call (e.g., SequentialAgent calling sub-agents)
                    is_agent_comm = True
                    # Source: parent invoke_agent span
                    parent_attrs = parent_span.get('attributes', {})
                    source_agent = parent_attrs.get('gen_ai.agent.name') or parent_span.get('agent_name', 'unknown')
                    # Target: current invoke_agent span
                    target_agent = attrs.get('gen_ai.agent.name') or span.get('agent_name', 'unknown')
                    if target_agent == 'unknown':
                        # Extract from span name
                        parts = span.get('name', '').split()
                        if len(parts) > 1:
                            target_agent = parts[-1]

        if is_agent_comm and source_agent != 'unknown' and target_agent != 'unknown':
            # For inter-agent communication, we need to distinguish between:
            # 1. Communication overhead (actual time to pass data between agents) - should be < 1ms for in-process calls
            # 2. Total sub-agent execution time (including LLM calls) - this is what the span duration represents

            # For in-process calls (monolithic), the communication overhead is minimal (< 1ms)
            # The span duration includes the entire sub-agent execution, which is not just communication overhead
            # However, for the purpose of "inter-agent delay", we should use a small fixed value for in-process calls
            # or calculate the actual communication overhead by subtracting child spans

            span_id = span.get('span_id')
            duration_ns = _safe_int(span.get('duration_ns'))

            # Calculate communication overhead: duration minus child spans (LLM calls, tool calls, etc.)
            # This gives us the actual communication overhead + agent processing time
            child_duration = 0
            for child_span in traces:
                if child_span.get('parent_span_id') == span_id:
                    child_duration += _safe_int(child_span.get('duration_ns'))

            # Communication overhead = total duration - child spans duration
            # For in-process calls, this should be very small (< 1ms)
            # For distributed calls, this includes network latency
            communication_overhead_ns = max(0, duration_ns - child_duration)
            communication_overhead_ms = communication_overhead_ns / 1e6

            # Use communication overhead as inter-agent delay (not the full duration)
            # This represents the actual time spent in communication, not the sub-agent execution time
            if communication_overhead_ms > 0:
                delays['inter_agent_delay'].append(communication_overhead_ms)
                inter_agent_delays[(source_agent, target_agent)].append(communication_overhead_ms)

    # Agent processing delay: invoke_agent spans minus child spans
    # Also support other agent span patterns (e.g., OpenAIAugmentedLLM.*.generate spans, tot.* spans)
    for span in traces:
        span_name = _safe_lower(span.get('name'))
        attrs = span.get('attributes', {})

        # Check if this is an agent span
        # Priority 1: Check gen_ai.operation.name if available
        operation_name = _safe_lower(attrs.get('gen_ai.operation.name'))
        is_agent_span = operation_name == 'invoke_agent'

        # Priority 2: Check span name patterns
        if not is_agent_span:
            is_agent_span = (
                'invoke_agent' in span_name or
                ('augmentedllm' in span_name and 'generate' in span_name) or
                (attrs.get('gen_ai.agent.name') and 'generate' in span_name)
            )

        # Priority 3: Check if span has gen_ai.agent.name and has child spans (for tot.*, etc.)
        # This handles cases where agent spans don't follow standard naming patterns
        if not is_agent_span:
            agent_name = attrs.get('gen_ai.agent.name') or span.get('agent_name')
            if agent_name:
                # Check if this span has child spans (indicating it's a parent/agent span)
                span_id = span.get('span_id')
                has_children = any(s.get('parent_span_id') == span_id for s in traces)
                if has_children:
                    is_agent_span = True

        if is_agent_span:
            span_id = span.get('span_id')
            duration_ns = _safe_int(span.get('duration_ns'))

            # Find child spans
            child_duration = 0
            for child_span in traces:
                if child_span.get('parent_span_id') == span_id:
                    child_duration += _safe_int(child_span.get('duration_ns'))

            # Processing delay = total duration - child spans duration
            processing_delay = max(0, duration_ns - child_duration) / 1e6  # Convert to ms
            if processing_delay > 0:
                delays['agent_processing_delay'].append(processing_delay)

                # Attribute to agent
                agent_name = attrs.get('gen_ai.agent.name') or span.get('agent_name', 'unknown')
                if agent_name == 'unknown':
                    # Try to extract from span name (e.g., "OpenAIAugmentedLLM.data_collector.generate")
                    parts = span.get('name', '').split('.')
                    if len(parts) >= 2:
                        agent_name = parts[1]  # Extract agent name from span name

                if agent_name != 'unknown':
                    per_agent_delays[agent_name].append(processing_delay)

    # E2E delay: sum of all invocation spans or top-level spans (total E2E delay for all requests)
    # Note: In distributed systems, invocation spans may have parent_span_id (they're not root spans)
    # So we need to find all invocation spans, not just root spans
    # Also support other top-level span patterns if invocation spans don't exist
    invocation_spans = [span for span in traces if span.get('name') == 'invocation' or
                       (_safe_lower(span.get('name')) == 'invocation')]

    if not invocation_spans:
        # Fallback 1: use root spans (spans with no parent) as E2E delay
        root_spans = [span for span in traces if not span.get('parent_span_id')]
        if root_spans:
            invocation_spans = root_spans
        else:
            # Fallback 2: use top-level spans (spans whose parent is not in traces)
            # This handles cases where parent spans are in different trace files
            span_dict_for_lookup = {span['span_id']: span for span in traces}
            top_level_spans = [span for span in traces
                             if span.get('parent_span_id') and
                             span.get('parent_span_id') not in span_dict_for_lookup]
            if top_level_spans:
                # Group by trace_id to get one span per trace/request
                trace_groups = {}
                for span in top_level_spans:
                    trace_id = span.get('trace_id')
                    if trace_id not in trace_groups:
                        trace_groups[trace_id] = span
                    # Keep the span with longest duration per trace
                    elif _safe_int(span.get('duration_ns')) > _safe_int(trace_groups[trace_id].get('duration_ns')):
                        trace_groups[trace_id] = span
                invocation_spans = list(trace_groups.values())

    if invocation_spans:
        # Calculate total E2E delay as sum of all individual request delays
        total_e2e_delay = sum(_safe_int(span.get('duration_ns')) for span in invocation_spans) / 1e6
        delays['e2e_delay'].append(total_e2e_delay)  # Single value: total of all requests

    return delays, dict(per_agent_delays), dict(inter_agent_delays), dict(agent_llm_delays_detail)


def extract_message_sizes(traces: List[Dict]) -> Tuple[Dict[str, List[float]], Dict[str, Dict[str, List[float]]], Dict[str, Dict[str, List[float]]], Dict[str, Dict[str, List[float]]]]:
    """Extract message sizes from traces.

    Returns:
        (component_sizes, per_agent_sizes, inter_agent_sizes, agent_llm_sizes)
        component_sizes: {
            'inter_agent_input': [sizes in KB],
            'inter_agent_output': [sizes in KB],
            'agent_llm_input': [sizes in KB],
            'agent_llm_output': [sizes in KB]
        }
        per_agent_sizes: {agent_name: {'input': [sizes], 'output': [sizes]}}
        inter_agent_sizes: {(source, target): {'input': [sizes], 'output': [sizes]}}
        agent_llm_sizes: {agent_name: {'input': [sizes], 'output': [sizes]}}
    """
    sizes = {
        'inter_agent_input': [],
        'inter_agent_output': [],
        'agent_llm_input': [],
        'agent_llm_output': []
    }

    span_dict = {span['span_id']: span for span in traces}
    per_agent_sizes = defaultdict(lambda: {'input': [], 'output': []})
    inter_agent_sizes = defaultdict(lambda: {'input': [], 'output': []})
    agent_llm_sizes = defaultdict(lambda: {'input': [], 'output': []})

    for span in traces:
        comm = span.get('communication', {})
        input_size = _safe_int(comm.get('input_message_size_bytes'))
        output_size = _safe_int(comm.get('output_message_size_bytes'))

        if input_size > 0 or output_size > 0:
            span_name = _safe_lower(span.get('name'))
            attrs = span.get('attributes', {})
            is_agent_comm = False
            source_agent = 'unknown'
            target_agent = 'unknown'

            # Method 1: Check execute_tool spans (for AgentTool in marketing-agency or send_message in distributed systems)
            if 'execute_tool' in span_name:
                tool_type = attrs.get('gen_ai.tool.type', '')
                is_agent_comm = comm.get('is_agent_communication', False) or tool_type == 'AgentTool'

                # Also check if it's a send_message tool (common in distributed systems)
                if not is_agent_comm:
                    tool_name = _safe_lower(attrs.get('gen_ai.tool.name'))
                    if 'message' in tool_name or 'agent' in tool_name:
                        is_agent_comm = True

                if is_agent_comm:
                    # Find source agent by traversing up to find invoke_agent span
                    current_id = span.get('parent_span_id')
                    depth = 0
                    while current_id and depth < 10:  # Limit depth to avoid infinite loops
                        if current_id in span_dict:
                            parent_span = span_dict[current_id]
                            if 'invoke_agent' in _safe_lower(parent_span.get('name')):
                                parent_attrs = parent_span.get('attributes', {})
                                source_agent = parent_attrs.get('gen_ai.agent.name') or parent_span.get('agent_name', 'unknown')
                                break
                            current_id = parent_span.get('parent_span_id')
                            depth += 1
                        else:
                            break

                    # Find target agent from span name or attributes
                    target_agent = attrs.get('gen_ai.agent.name') or attrs.get('adk.agent.name') or span.get('agent_name', 'unknown')

                    # For send_message tool in distributed systems, extract agent_name from tool_call_args
                    if target_agent == 'unknown' and 'send_message' in span_name:
                        tool_call_args = attrs.get('gcp.vertex.agent.tool_call_args', '')
                        if tool_call_args:
                            try:
                                import json
                                args = json.loads(tool_call_args) if isinstance(tool_call_args, str) else tool_call_args
                                if isinstance(args, dict) and 'agent_name' in args:
                                    target_agent = args['agent_name']
                            except:
                                pass

                    # Fallback: try to extract from span name if not found in attributes
                    if target_agent == 'unknown':
                        match = re.search(r'execute_tool\s+(\w+)', span.get('name', ''), re.IGNORECASE)
                        if match:
                            target_agent = match.group(1)

            # Method 2: Check invoke_agent spans with parent invoke_agent (for SequentialAgent/LoopAgent in image-scoring)
            elif 'invoke_agent' in span_name:
                # Check if parent span is also an invoke_agent (indicating agent-to-agent call)
                parent_id = span.get('parent_span_id')
                if parent_id and parent_id in span_dict:
                    parent_span = span_dict[parent_id]
                    parent_name = _safe_lower(parent_span.get('name'))
                    if 'invoke_agent' in parent_name:
                        # This is an agent-to-agent call (e.g., SequentialAgent calling sub-agents)
                        is_agent_comm = True
                        # Source: parent invoke_agent span
                        parent_attrs = parent_span.get('attributes', {})
                        source_agent = parent_attrs.get('gen_ai.agent.name') or parent_span.get('agent_name', 'unknown')
                        # Target: current invoke_agent span
                        target_agent = attrs.get('gen_ai.agent.name') or span.get('agent_name', 'unknown')
                        if target_agent == 'unknown':
                            # Extract from span name
                            parts = span.get('name', '').split()
                            if len(parts) > 1:
                                target_agent = parts[-1]

                        # For invoke_agent spans, try to get message size from communication metrics
                        # Similar to execute_tool spans, but for invoke_agent spans in monolithic systems
                        comm = span.get('communication', {})
                        input_size = _safe_int(comm.get('input_message_size_bytes'))
                        output_size = _safe_int(comm.get('output_message_size_bytes'))

                        # If not found in communication metrics, try to get from attributes (same as execute_tool)
                        if input_size == 0 and output_size == 0:
                            tool_call_args = attrs.get('gcp.vertex.agent.tool_call_args', '')
                            if tool_call_args and isinstance(tool_call_args, str) and tool_call_args != "{}":
                                input_size = len(tool_call_args.encode('utf-8'))

                            tool_response = attrs.get('gcp.vertex.agent.tool_response', '')
                            if tool_response and isinstance(tool_response, str) and tool_response != "{}":
                                output_size = len(tool_response.encode('utf-8'))
                            input_size = _safe_int(input_size)
                            output_size = _safe_int(output_size)

                        # For in-process calls (SequentialAgent/LoopAgent), message size is typically 0
                        # because data is passed as Python objects, not serialized messages
                        # We still record the communication for completeness, even if size is 0
                        if input_size == 0 and output_size == 0 and comm.get('is_in_process_call', False):
                            # This is an in-process call with no serialized message data
                            # We still want to track it as inter-agent communication
                            pass

            if is_agent_comm:
                # Inter-agent communication
                input_size = _safe_int(input_size)
                output_size = _safe_int(output_size)
                input_kb = input_size / 1024
                output_kb = output_size / 1024

                if input_size > 0:
                    sizes['inter_agent_input'].append(input_kb)
                if output_size > 0:
                    sizes['inter_agent_output'].append(output_kb)

                if source_agent != 'unknown':
                    if input_size > 0:
                        per_agent_sizes[source_agent]['output'].append(input_kb)
                    if output_size > 0:
                        per_agent_sizes[target_agent]['input'].append(output_kb)

                if source_agent != 'unknown' and target_agent != 'unknown':
                    # Record inter-agent communication even if message size is 0
                    # For in-process calls, this represents the communication relationship
                    if input_size > 0:
                        inter_agent_sizes[(source_agent, target_agent)]['input'].append(input_kb)
                    elif comm.get('is_in_process_call', False):
                        # For in-process calls with no serialized message, record 0
                        inter_agent_sizes[(source_agent, target_agent)]['input'].append(0.0)

                    if output_size > 0:
                        inter_agent_sizes[(source_agent, target_agent)]['output'].append(output_kb)
                    elif comm.get('is_in_process_call', False):
                        # For in-process calls with no serialized message, record 0
                        inter_agent_sizes[(source_agent, target_agent)]['output'].append(0.0)

            else:
                # Check if this is an LLM call span
                # Priority 1: Use gen_ai.operation.name if available (per template)
                operation_name = _safe_lower(attrs.get('gen_ai.operation.name'))
                is_llm_span = operation_name == 'call_llm'

                # Priority 2: Fallback to span name pattern matching (for compatibility)
                if not is_llm_span:
                    span_name = _safe_lower(span.get('name'))
                    is_llm_span = (
                        'call_llm' in span_name or
                        'openai.chat' in span_name or
                        ('generate' in span_name and 'llm' in span_name) or
                        attrs.get('gen_ai.usage.input_tokens') or
                        attrs.get('gen_ai.usage.output_tokens')
                    )

                if is_llm_span:
                    # Agent-LLM communication
                    input_size = _safe_int(input_size)
                    output_size = _safe_int(output_size)
                    input_kb = input_size / 1024
                    output_kb = output_size / 1024

                    if input_size > 0:
                        sizes['agent_llm_input'].append(input_kb)
                    if output_size > 0:
                        sizes['agent_llm_output'].append(output_kb)

                    # Find agent name from span attributes or parent spans (search up the tree)
                    agent_name = 'unknown'
                    # First try current span
                    agent_name = attrs.get('gen_ai.agent.name') or span.get('agent_name', 'unknown')

                    # If not found, search up parent chain
                    if agent_name == 'unknown':
                        current = span
                        max_levels = 5
                        for _ in range(max_levels):
                            parent_id = current.get('parent_span_id')
                            if not parent_id or parent_id not in span_dict:
                                break

                            parent_span = span_dict[parent_id]
                            parent_name = _safe_lower(parent_span.get('name'))
                            parent_attrs = parent_span.get('attributes', {})

                            # Check if parent has agent name
                            parent_agent_name = parent_attrs.get('gen_ai.agent.name') or parent_span.get('agent_name')
                            if parent_agent_name:
                                agent_name = parent_agent_name
                                break

                            # Check if parent is an agent span
                            if ('invoke_agent' in parent_name or
                                'agent' in parent_name or
                                'augmentedllm' in parent_name):
                                # Try to extract from parent name or attributes
                                if parent_agent_name:
                                    agent_name = parent_agent_name
                                    break

                            current = parent_span

                    if agent_name != 'unknown':
                        if input_size > 0:
                            agent_llm_sizes[agent_name]['input'].append(input_kb)
                        if output_size > 0:
                            agent_llm_sizes[agent_name]['output'].append(output_kb)

    return sizes, dict(per_agent_sizes), dict(inter_agent_sizes), dict(agent_llm_sizes)


def _is_real_agent(agent_name: Optional[str]) -> bool:
    """Check if agent_name represents a real agent, not an internal operation."""
    if not agent_name:
        return False
    agent_lower = agent_name.lower().strip()

    if agent_lower in ("unknown", ""):
        return False

    operation_patterns = [
        " create ", " process ", " publish ", " send ", " receive ",
        " create_", " process_", " publish_", " send_", " receive_",
    ]
    if any(pattern in agent_lower for pattern in operation_patterns):
        return False

    infrastructure_keywords = [
        "topic", "manager", "coordinator", "router", "dispatcher", "groupchat", "group-chat"
    ]
    if any(keyword in agent_lower for keyword in infrastructure_keywords):
        if not agent_lower.endswith(("_agent", "agent")) and "_agent" not in agent_lower:
            return False

    if "system" in agent_lower or "service" in agent_lower:
        words = agent_lower.split("-") + agent_lower.split("_")
        if len(words) > 2:
            if not agent_lower.endswith(("_agent", "agent")):
                return False
        if agent_lower.endswith(("-system", "_system")):
            return False

    return True


def _should_filter_internal_agents(traces: List[Dict]) -> bool:
    """Detect traces that include internal Autogen-style agent labels."""
    for span in traces:
        attrs = span.get("attributes", {})
        if "sender_agent_type" in attrs or "recipient_agent_type" in attrs:
            return True
        name = _safe_lower(span.get("name"))
        if name.startswith("autogen "):
            return True
        agent_name = (
            attrs.get("gen_ai.agent.name")
            or span.get("agent_name")
            or attrs.get("agent.name")
        )
        if agent_name and "autogen " in agent_name.lower():
            return True
        resource_attrs = span.get("resource", {}).get("attributes", {})
        service_name = resource_attrs.get("service.name", "")
        if isinstance(service_name, str) and "autogen" in service_name.lower():
            return True
    return False


def _normalize_agent_label(agent_name: Optional[str]) -> str:
    if not agent_name:
        return "unknown"
    name = agent_name.strip()
    name = re.sub(
        r"_([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$",
        "",
        name,
        flags=re.IGNORECASE,
    )
    name = name.replace("_agent", "").replace("-agent", "")
    name = name.replace("_", "-").replace(" ", "-")
    return name.lower()


def extract_call_graph(traces: List[Dict]) -> set:
    """Extract call graph edges (agent pairs) from traces.

    Supports both monolithic systems (AgentTool via execute_tool) and distributed systems (invoke_agent spans).
    Only includes agent-to-agent calls, not tool calls (FunctionTool).

    Returns:
        Set of tuples (source_agent, target_agent) representing call graph edges
    """
    edges = set()
    span_dict = {span['span_id']: span for span in traces}

    # Method 0: Extract call graph based on agent name hierarchy (most reliable for all systems)
    # This method works even when gen_ai.tool.type or communication.is_agent_communication are missing
    for span in traces:
        attrs = span.get('attributes', {})
        # Prefer gen_ai.agent.name as it's more reliable (standard OTEL field)
        # Only fall back to agent_name if gen_ai.agent.name is not available
        current_agent = attrs.get('gen_ai.agent.name')
        if not current_agent:
            # Fallback: check agent_name but filter out internal operations
            candidate = span.get('agent_name')
            if candidate and _is_real_agent(candidate):
                current_agent = candidate

        if current_agent and _is_real_agent(current_agent):
            # Find parent agent by traversing up the span tree
            # Traverse until we find a span with a real agent name
            parent_id = span.get('parent_span_id')
            depth = 0
            parent_agent_found = None

            while parent_id and depth < 15:  # Increased depth limit
                if parent_id in span_dict:
                    parent = span_dict[parent_id]
                    parent_attrs = parent.get('attributes', {})
                    # Prefer gen_ai.agent.name (most reliable)
                    parent_agent = parent_attrs.get('gen_ai.agent.name')
                    if not parent_agent:
                        # Only use agent_name if it's a real agent (not internal operation)
                        candidate = parent.get('agent_name')
                        if candidate and _is_real_agent(candidate):
                            parent_agent = candidate

                    # If we found a real parent agent, check if it's different from current
                    if parent_agent and _is_real_agent(parent_agent):
                        if parent_agent != current_agent:
                            parent_agent_found = parent_agent
                            break  # Found a valid parent agent, stop traversing
                        # If same agent, continue traversing up (might be nested calls)
                        parent_id = parent.get('parent_span_id')
                        depth += 1
                    else:
                        # No agent found at this level, continue up
                        parent_id = parent.get('parent_span_id')
                        depth += 1
                else:
                    break

            # If we found a valid parent agent, add the edge
            # But only if both source and target are real agents (not system-level)
            if parent_agent_found:
                # Normalize agent names
                source_agent = parent_agent_found.replace('_agent', '').replace('-agent', '').replace('_', '-').replace(' ', '-').lower()
                target_agent = current_agent.replace('_agent', '').replace('-agent', '').replace('_', '-').replace(' ', '-').lower()

                # Double-check both are real agents (not system/service names)
                # Skip system-level sources (like 'autogen-multi-system')
                if 'system' in source_agent or 'service' in source_agent:
                    # This is a system-level call, not agent-to-agent, skip it
                    pass
                # Skip self-loops
                elif source_agent != target_agent:
                    edges.add((source_agent, target_agent))

    # Continue with existing methods for additional edges (execute_tool, invoke_agent)
    for span in traces:
        span_name = _safe_lower(span.get('name'))
        comm = span.get('communication', {})
        attrs = span.get('attributes', {})

        # Method 1: Check execute_tool spans (for monolithic systems with AgentTool)
        if 'execute_tool' in span_name:
            tool_type = attrs.get('gen_ai.tool.type', '')
            tool_name = _safe_lower(attrs.get('gen_ai.tool.name'))

            # Explicitly exclude FunctionTool - these are tools, not agents
            # Exception: send_message in distributed systems (even if marked as FunctionTool)
            if tool_type == 'FunctionTool':
                # Only allow send_message tool for agent communication in distributed systems
                if 'send_message' not in tool_name:
                    continue

            # Check if it's agent communication: either marked as such, or is AgentTool type
            is_agent_comm = comm.get('is_agent_communication', False) or tool_type == 'AgentTool'

            # Also check if it's a send_message tool (common in distributed systems)
            # Even if tool_type is FunctionTool, send_message is used for agent communication
            if not is_agent_comm:
                if 'message' in tool_name or 'agent' in tool_name:
                    is_agent_comm = True

            # For send_message tools in distributed systems, always treat as agent communication
            # even if tool_type is FunctionTool
            if tool_type == 'FunctionTool' and 'send_message' in tool_name:
                is_agent_comm = True

            # Fallback: If tool_type is missing, check if we can find agent-to-agent relationship
            # by checking parent-child agent names
            if not is_agent_comm and not tool_type:
                # Try to find source agent from parent chain
                source_agent = None
                current_id = span.get('parent_span_id')
                depth = 0
                while current_id and depth < 10:
                    if current_id in span_dict:
                        parent_span = span_dict[current_id]
                        parent_attrs = parent_span.get('attributes', {})
                        parent_agent = parent_attrs.get('gen_ai.agent.name') or parent_span.get('agent_name')
                        if parent_agent:
                            source_agent = parent_agent
                            break
                        current_id = parent_span.get('parent_span_id')
                        depth += 1

                # Try to find target agent from current span or child spans
                target_agent = attrs.get('gen_ai.agent.name') or span.get('agent_name')
                if not target_agent:
                    # Check child spans
                    span_id = span.get('span_id')
                    for child_span in traces:
                        if child_span.get('parent_span_id') == span_id:
                            child_attrs = child_span.get('attributes', {})
                            child_agent = child_attrs.get('gen_ai.agent.name') or child_span.get('agent_name')
                            if child_agent:
                                target_agent = child_agent
                                break

                # If we found both source and target agents, and they're different, it's agent communication
                if source_agent and target_agent and source_agent != target_agent:
                    is_agent_comm = True

            # Final check: if still not agent communication, skip
            if not is_agent_comm:
                continue

            if is_agent_comm:
                # Find source and target agents
                source_agent = 'unknown'
                target_agent = 'unknown'

                # Find source agent by traversing up to find invoke_agent span
                # This is the most reliable method for monolithic systems (marketing-agency)
                # Continue traversing even if we find call_llm spans in between
                current_id = span.get('parent_span_id')
                depth = 0
                while current_id and depth < 10:
                    if current_id in span_dict:
                        parent_span = span_dict[current_id]
                        parent_name = _safe_lower(parent_span.get('name'))
                        if 'invoke_agent' in parent_name:
                            parent_attrs = parent_span.get('attributes', {})
                            parent_agent = parent_attrs.get('gen_ai.agent.name') or parent_span.get('agent_name', 'unknown')
                            if parent_agent != 'unknown':
                                source_agent = parent_agent.replace('_agent', '').replace('-agent', '').replace('_', '-').replace(' ', '-').lower()
                            break
                        # Continue traversing up even if we find call_llm or other spans
                        current_id = parent_span.get('parent_span_id')
                        depth += 1
                    else:
                        break

                # For send_message tools in distributed systems, use service.name as source agent
                # This is more reliable than traversing parent spans for distributed systems
                # Only use service.name if we couldn't find invoke_agent in parent chain
                if source_agent == 'unknown' and ('send_message' in span_name or 'send_message' in tool_name):
                    resource_attrs = span.get('resource', {}).get('attributes', {})
                    service_name = resource_attrs.get('service.name', '')
                    if service_name and service_name not in ['unknown', '']:
                        source_agent = service_name.replace('_agent', '').replace('-agent', '').replace('_', '-').replace(' ', '-').lower()

                # Find target agent - for AgentTool, extract from span name (e.g., "execute_tool domain_create_agent")
                # For send_message, extract from tool_call_args
                span_name_full = span.get('name', '')

                # For send_message tool in distributed systems, extract agent_name from tool_call_args
                if 'send_message' in span_name or 'send_message' in tool_name:
                    tool_call_args = attrs.get('gcp.vertex.agent.tool_call_args', '')
                    if tool_call_args:
                        try:
                            import json
                            args = json.loads(tool_call_args) if isinstance(tool_call_args, str) else tool_call_args
                            if isinstance(args, dict) and 'agent_name' in args:
                                target_agent = args['agent_name']
                                # Normalize agent name: "Content Planner Agent" -> "content-planner"
                                target_agent = target_agent.replace(' Agent', '').replace('_', '-').replace(' ', '-').lower()
                        except:
                            pass

                # For AgentTool in monolithic systems, extract from span name
                if target_agent == 'unknown':
                    # Format: "execute_tool <agent_name>"
                    match = re.search(r'execute_tool\s+(\w+)', span_name_full, re.IGNORECASE)
                    if match:
                        target_agent = match.group(1)
                        # For monolithic systems, keep the original agent name format (with underscores)
                        # e.g., "domain_create_agent" -> "domain_create_agent" (not "domain-create")
                        # But normalize to lowercase
                        target_agent = target_agent.lower()
                    else:
                        # Fallback to attributes
                        target_agent = attrs.get('gen_ai.agent.name') or attrs.get('adk.agent.name') or span.get('agent_name', 'unknown')
                        if target_agent != 'unknown':
                            target_agent = target_agent.lower()

                # Normalize source agent name for consistency
                if source_agent != 'unknown':
                    source_agent = source_agent.replace('_agent', '').replace('-agent', '').replace('_', '-').replace(' ', '-').lower()

                if source_agent != 'unknown' and target_agent != 'unknown' and source_agent != target_agent:
                    # In distributed systems, 'routing' is typically an internal agent of 'coordinator'
                    # Replace 'routing' with 'coordinator' to avoid duplicate nodes
                    if target_agent == 'routing':
                        target_agent = 'coordinator'
                    if source_agent == 'routing':
                        source_agent = 'coordinator'

                    # Skip self-loops after normalization
                    if source_agent == target_agent:
                        continue

                    edges.add((source_agent, target_agent))

        # Method 2: Check invoke_agent spans
        # Two cases:
        # 1. Distributed systems: invoke_agent spans in remote agent traces (handled below)
        # 2. Monolithic systems with SequentialAgent/LoopAgent: invoke_agent spans with parent invoke_agent (handled here)
        elif 'invoke_agent' in span_name:
            target_agent = attrs.get('gen_ai.agent.name') or span.get('agent_name', 'unknown')

            # Try to extract from span name if not in attributes
            if target_agent == 'unknown':
                # Format: "invoke_agent <agent_name>"
                parts = span.get('name', '').split()
                if len(parts) > 1:
                    target_agent = parts[-1]

            # Filter out internal operations
            if not _is_real_agent(target_agent):
                continue

            # Normalize target agent name
            normalized_target = target_agent.replace('_agent', '').replace('-agent', '').replace('_', '-').lower()

            # Find source agent from parent span
            source_agent = 'unknown'
            resource_attrs = span.get('resource', {}).get('attributes', {})
            service_name = resource_attrs.get('service.name', '')
            normalized_service = service_name.replace('_agent', '').replace('-agent', '').replace('_', '-').lower() if service_name else ''

            # Check if parent span is also an invoke_agent (indicating agent-to-agent call in monolithic system)
            parent_id = span.get('parent_span_id')
            if parent_id and parent_id in span_dict:
                parent_span = span_dict[parent_id]
                parent_name = _safe_lower(parent_span.get('name'))
                if 'invoke_agent' in parent_name:
                    # This is an agent-to-agent call (e.g., SequentialAgent calling sub-agents in image-scoring)
                    parent_attrs = parent_span.get('attributes', {})
                    parent_agent = parent_attrs.get('gen_ai.agent.name') or parent_span.get('agent_name', 'unknown')
                    if parent_agent != 'unknown':
                        source_agent = parent_agent.replace('_agent', '').replace('-agent', '').replace('_', '-').lower()

            # If we didn't find a parent invoke_agent, this might be:
            # 1. Entry point in monolithic system (marketing-agency) - skip it
            # 2. Remote agent invocation in distributed system - handle below

            # Skip if this is likely an entry point in monolithic system
            if source_agent == 'unknown':
                # If service.name and target agent name refer to the same agent, this is likely an entry point
                if normalized_service == normalized_target:
                    continue
                # Special case: marketing-agency entry point
                if normalized_service and normalized_service in ['marketing-agency', 'marketing-agency']:
                    continue
                # For distributed systems, use service.name as source (but filter out system-level names)
                # Only use service.name if it represents a real agent, not a system
                if service_name and service_name not in ['unknown', ''] and _is_real_agent(normalized_service):
                    source_agent = normalized_service

            # Normalize both agent names for consistency
            if source_agent != 'unknown':
                source_agent = source_agent.replace('_agent', '').replace('-agent', '').replace('_', '-').replace(' ', '-').lower()
            if target_agent != 'unknown':
                target_agent = target_agent.replace('_agent', '').replace('-agent', '').replace('_', '-').replace(' ', '-').lower()

            # Only add edge if both agents are known and different
            if source_agent != 'unknown' and target_agent != 'unknown' and source_agent != target_agent:
                # In distributed systems, 'routing' is typically an internal agent of 'coordinator'
                # Replace 'routing' with 'coordinator' to avoid duplicate nodes
                if target_agent == 'routing':
                    target_agent = 'coordinator'
                if source_agent == 'routing':
                    source_agent = 'coordinator'

                # Skip self-loops after normalization
                if source_agent == target_agent:
                    continue

                edges.add((source_agent, target_agent))

    # If no edges were found but we have agents, we might have a system where agents don't call each other directly
    # (e.g., all agents are called by a coordinator). In this case, we still want to show the agents as nodes.
    # However, for call graph visualization, edges are required. So we'll return empty edges
    # and the visualization code can handle showing just nodes if needed.
    # For now, we return edges as-is. If edges is empty, it means no agent-to-agent calls were found,
    # which is valid for some system architectures (e.g., all agents called by a coordinator).

    return edges


def extract_call_sequence(traces: List[Dict]) -> List[tuple]:
    """Extract call sequence (ordered list of agent-to-agent calls) from traces.

    Returns the sequence of (source_agent, target_agent) tuples in chronological order.
    This preserves the calling order, unlike extract_call_graph which returns a set.
    Supports both monolithic systems (AgentTool via execute_tool) and distributed systems (invoke_agent spans).

    Returns:
        List of tuples (source_agent, target_agent) in chronological order
    """
    call_sequence = []
    span_dict = {span['span_id']: span for span in traces}
    filter_internal_agents = _should_filter_internal_agents(traces)
    used_sender_spans = set()

    # Collect all spans that represent agent communication
    agent_calls = []

    if filter_internal_agents:
        for span in traces:
            attrs = span.get("attributes", {})
            sender = attrs.get("sender_agent_type")
            recipient = attrs.get("recipient_agent_type")
            if not sender or not recipient:
                continue
            source_agent = _normalize_agent_label(sender)
            target_agent = _normalize_agent_label(recipient)
            if source_agent == "unknown" or target_agent == "unknown":
                continue
            if not _is_real_agent(target_agent):
                continue
            if source_agent == target_agent:
                continue
            start_time = _safe_int(span.get("start_time"))
            agent_calls.append((start_time, source_agent, target_agent))
            span_id = span.get("span_id")
            if span_id:
                used_sender_spans.add(span_id)

    # Method 0: Extract call sequence based on agent name hierarchy (most reliable for all systems)
    # This method works even when gen_ai.tool.type or communication.is_agent_communication are missing
    for span in traces:
        if filter_internal_agents and span.get("span_id") in used_sender_spans:
            continue
        attrs = span.get('attributes', {})
        current_agent = attrs.get('gen_ai.agent.name') or span.get('agent_name')

        if current_agent:
            if filter_internal_agents and not _is_real_agent(current_agent):
                continue
            # Find parent agent by traversing up the span tree
            parent_id = span.get('parent_span_id')
            depth = 0
            while parent_id and depth < 10:
                if parent_id in span_dict:
                    parent = span_dict[parent_id]
                    parent_attrs = parent.get('attributes', {})
                    parent_agent = parent_attrs.get('gen_ai.agent.name') or parent.get('agent_name')
                    if filter_internal_agents and parent_agent and not _is_real_agent(parent_agent):
                        parent_id = parent.get('parent_span_id')
                        depth += 1
                        continue

                    if parent_agent and parent_agent != current_agent:
                        # Found agent-to-agent call relationship
                        # Normalize agent names
                        source_agent = parent_agent.replace('_agent', '').replace('-agent', '').replace('_', '-').replace(' ', '-').lower()
                        target_agent = current_agent.replace('_agent', '').replace('-agent', '').replace('_', '-').replace(' ', '-').lower()

                        # Skip self-loops
                        if source_agent != target_agent:
                            # Store with timestamp for sorting
                            start_time = _safe_int(span.get('start_time'))
                            agent_calls.append((start_time, source_agent, target_agent))
                        break

                    parent_id = parent.get('parent_span_id')
                    depth += 1
                else:
                    break

    # Continue with existing methods for additional edges (execute_tool, invoke_agent)
    for span in traces:
        span_name = _safe_lower(span.get('name'))
        comm = span.get('communication', {})
        attrs = span.get('attributes', {})
        tool_name = _safe_lower(attrs.get('gen_ai.tool.name')) if attrs.get('gen_ai.tool.name') else ''

        # Method 1: Check execute_tool spans (for monolithic systems with AgentTool)
        if 'execute_tool' in span_name:
            if filter_internal_agents and span.get("span_id") in used_sender_spans:
                continue
            tool_type = attrs.get('gen_ai.tool.type', '')

            # Check if it's agent communication
            is_agent_comm = comm.get('is_agent_communication', False) or tool_type == 'AgentTool'

            if not is_agent_comm:
                if 'message' in tool_name or 'agent' in tool_name:
                    is_agent_comm = True

            # For send_message tools in distributed systems, always treat as agent communication
            if tool_type == 'FunctionTool' and 'send_message' in tool_name:
                is_agent_comm = True

            # Skip non-agent FunctionTool calls (but allow send_message)
            if tool_type == 'FunctionTool' and not is_agent_comm:
                continue

            if is_agent_comm:
                # Find source and target agents (same logic as extract_call_graph)
                source_agent = 'unknown'
                target_agent = 'unknown'

                # For send_message tools in distributed systems, use service.name as source agent
                resource_attrs = span.get('resource', {}).get('attributes', {})
                service_name = resource_attrs.get('service.name', '')
                if service_name and service_name not in ['unknown', '']:
                    if filter_internal_agents and not _is_real_agent(service_name):
                        service_name = ''
                    else:
                        source_agent = service_name.replace('_agent', '').replace('-agent', '').replace('_', '-').replace(' ', '-').lower()

                # If service.name is not available, find source agent by traversing up to find invoke_agent span
                if source_agent == 'unknown':
                    current_id = span.get('parent_span_id')
                    depth = 0
                    while current_id and depth < 10:
                        if current_id in span_dict:
                            parent_span = span_dict[current_id]
                            if 'invoke_agent' in _safe_lower(parent_span.get('name')):
                                parent_attrs = parent_span.get('attributes', {})
                                parent_agent = parent_attrs.get('gen_ai.agent.name') or parent_span.get('agent_name', 'unknown')
                                if parent_agent != 'unknown':
                                    if filter_internal_agents and not _is_real_agent(parent_agent):
                                        parent_agent = 'unknown'
                                if parent_agent != 'unknown':
                                    source_agent = parent_agent.replace('_agent', '').replace('-agent', '').replace('_', '-').replace(' ', '-').lower()
                                break
                            current_id = parent_span.get('parent_span_id')
                            depth += 1
                        else:
                            break

                # Find target agent
                target_agent = attrs.get('gen_ai.agent.name') or attrs.get('adk.agent.name') or span.get('agent_name', 'unknown')
                if filter_internal_agents and not _is_real_agent(target_agent):
                    target_agent = 'unknown'

                if target_agent == 'unknown' and ('send_message' in span_name or 'send_message' in tool_name):
                    tool_call_args = attrs.get('gcp.vertex.agent.tool_call_args', '')
                    if tool_call_args:
                        try:
                            import json
                            args = json.loads(tool_call_args) if isinstance(tool_call_args, str) else tool_call_args
                            if isinstance(args, dict) and 'agent_name' in args:
                                target_agent = args['agent_name']
                                # Normalize agent name: "Content Planner Agent" -> "content-planner"
                                target_agent = target_agent.replace(' Agent', '').replace('_', '-').lower()
                        except:
                            pass

                if target_agent == 'unknown' and span_name:
                    match = re.search(r'execute_tool\s+(\w+)', span_name, re.IGNORECASE)
                    if match:
                        target_agent = match.group(1)
                        target_agent = target_agent.replace('_agent', '').replace('-agent', '').replace('_', '-').lower()

                # Normalize source and target agent names for consistency
                if source_agent != 'unknown':
                    source_agent = source_agent.replace('_agent', '').replace('-agent', '').replace('_', '-').lower()
                if target_agent != 'unknown':
                    target_agent = target_agent.replace('_agent', '').replace('-agent', '').replace('_', '-').lower()

                if source_agent != 'unknown' and target_agent != 'unknown' and source_agent != target_agent:
                    # Store with timestamp for sorting
                    start_time = _safe_int(span.get('start_time'))
                    agent_calls.append((start_time, source_agent, target_agent))

        # Method 2: Check invoke_agent spans (for distributed systems or SequentialAgent/LoopAgent)
        elif 'invoke_agent' in span_name:
            if filter_internal_agents and span.get("span_id") in used_sender_spans:
                continue
            target_agent = attrs.get('gen_ai.agent.name') or span.get('agent_name', 'unknown')

            # Try to extract from span name if not in attributes
            if target_agent == 'unknown':
                parts = span.get('name', '').split()
                if len(parts) > 1:
                    target_agent = parts[-1]
            if filter_internal_agents and not _is_real_agent(target_agent):
                continue

            # Check if parent span is also an invoke_agent (indicating agent-to-agent call in monolithic system)
            source_agent = 'unknown'
            parent_id = span.get('parent_span_id')
            if parent_id and parent_id in span_dict:
                parent_span = span_dict[parent_id]
                parent_name = _safe_lower(parent_span.get('name'))
                if 'invoke_agent' in parent_name:
                    # This is an agent-to-agent call (e.g., SequentialAgent calling sub-agents in image-scoring)
                    parent_attrs = parent_span.get('attributes', {})
                    parent_agent = parent_attrs.get('gen_ai.agent.name') or parent_span.get('agent_name', 'unknown')
                    if parent_agent != 'unknown':
                        if filter_internal_agents and not _is_real_agent(parent_agent):
                            parent_agent = 'unknown'
                    if parent_agent != 'unknown':
                        source_agent = parent_agent.replace('_agent', '').replace('-agent', '').replace('_', '-').lower()

            # If we didn't find a parent invoke_agent, this might be a distributed system call
            if source_agent == 'unknown':
                # Normalize target agent name
                normalized_target = target_agent.replace('_agent', '').replace('-agent', '').replace('_', '-').lower()

                # Find source agent from service.name
                resource_attrs = span.get('resource', {}).get('attributes', {})
                service_name = resource_attrs.get('service.name', '')

                # Normalize service name
                normalized_service = service_name.replace('_agent', '').replace('-agent', '').replace('_', '-').lower() if service_name else ''

                # Skip if service.name and target agent name refer to the same agent (entry point)
                if normalized_service == normalized_target:
                    continue

                # Skip marketing-agency entry point
                if normalized_service and normalized_service in ['marketing-agency', 'marketing-agency']:
                    continue

                if service_name and service_name not in ['unknown', '']:
                    if filter_internal_agents and not _is_real_agent(service_name):
                        service_name = ''
                    else:
                        source_agent = normalized_service

            # Normalize both agent names for consistency
            if source_agent != 'unknown':
                source_agent = source_agent.replace('_agent', '').replace('-agent', '').replace('_', '-').replace(' ', '-').lower()
            if target_agent != 'unknown':
                target_agent = target_agent.replace('_agent', '').replace('-agent', '').replace('_', '-').replace(' ', '-').lower()

            if source_agent != 'unknown' and target_agent != 'unknown' and source_agent != target_agent:
                # Store with timestamp for sorting
                start_time = _safe_int(span.get('start_time'))
                agent_calls.append((start_time, source_agent, target_agent))

    # Sort by timestamp and extract sequence
    agent_calls.sort(key=lambda x: x[0])
    call_sequence = [
        (src, tgt)
        for _, src, tgt in agent_calls
        if src != 'unknown' and tgt != 'unknown'
    ]

    return call_sequence


def extract_cpu_memory_usage(metrics: List[Dict]) -> Dict:
    """Extract CPU and memory usage from metrics (process-level only).

    In distributed systems, each agent has its own metrics file, so we extract
    per-agent CPU/memory usage. In monolithic systems, all metrics are from one process.

    Returns:
        {
            'cpu': {
                'process': [(timestamp, value)],  # All processes combined (for backward compatibility)
                'per_agent': {agent_name: [(timestamp, value)]}  # Per-agent CPU usage
            },
            'memory': {
                'process': [(timestamp, value_bytes)],  # All processes combined
                'per_agent': {agent_name: [(timestamp, value_bytes)]}  # Per-agent memory usage
            }
        }
    """
    usage = {
        'cpu': {'process': [], 'per_agent': defaultdict(list)},
        'memory': {'process': [], 'per_agent': defaultdict(list)}
    }

    for metric in metrics:
        metric_name = metric.get('metric_name', '')
        data_points = metric.get('data_points', [])

        # Extract agent name from resource attributes
        resource = metric.get('resource', {})
        resource_attrs = resource.get('attributes', {}) if resource else {}
        agent_name = resource_attrs.get('service.name', 'unknown')

        for dp in data_points:
            timestamp = dp.get('timestamp', 0)
            value = dp.get('value', 0)

            # Support both old and new metric name formats
            if metric_name == 'process.cpu.usage' or metric_name == 'process.cpu.utilization':
                usage['cpu']['process'].append((timestamp / 1e9, value))  # Convert to seconds
                usage['cpu']['per_agent'][agent_name].append((timestamp / 1e9, value))
            elif metric_name == 'process.memory.usage_bytes' or metric_name == 'process.memory.usage':
                mem_mb = value / (1024 * 1024)  # Convert to MB
                usage['memory']['process'].append((timestamp / 1e9, mem_mb))
                usage['memory']['per_agent'][agent_name].append((timestamp / 1e9, mem_mb))

    # Sort by timestamp
    for key in usage:
        for subkey in usage[key]:
            if isinstance(usage[key][subkey], list):
                usage[key][subkey].sort(key=lambda x: x[0])
            elif isinstance(usage[key][subkey], dict):
                for agent_name in usage[key][subkey]:
                    usage[key][subkey][agent_name].sort(key=lambda x: x[0])

    # Convert defaultdict to dict for JSON serialization
    usage['cpu']['per_agent'] = dict(usage['cpu']['per_agent'])
    usage['memory']['per_agent'] = dict(usage['memory']['per_agent'])

    return usage
