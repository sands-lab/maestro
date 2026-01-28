import re
def _start_time_sorted(dict: dict[str, dict]):
    for k, v in sorted(dict.items(), key=lambda item: item[1]['start_time']):
        yield k, v

def delay_extractor(_trace_dict):
    _return = []
    _temp_dict = {}
    whole_time = 0
    for k, v in _start_time_sorted(_trace_dict):
        if v['name'] == 'openai.chat':
            if _temp_dict:
                _return.append(_temp_dict)
            _temp_dict = {}
            current_span_id = k
            target_span_id = v['parent_span_id']
            while True:
                if 'invoke_agent' in _trace_dict[current_span_id]['name']:
                    agent_name = _trace_dict[current_span_id]['name'].split()[-1]
                    # == Processing Time ==
                    invoke_agent_time = _trace_dict[current_span_id]['duration_ns'] / 1e6 # ms
                    _invoke_agent_child_time_sum = 0
                    for _, _v in _trace_dict.items():
                        if _v['parent_span_id'] == current_span_id:
                            _invoke_agent_child_time_sum += _v['duration_ns'] / 1e6 # ms
                    processing_time = invoke_agent_time - _invoke_agent_child_time_sum
                    _temp_dict['agent-processing_time'] = processing_time
                    # == Processing Time ==
                    break
                current_span_id = target_span_id
                target_span_id = _trace_dict[current_span_id]['parent_span_id']
            # == Agent-LLM Delay ==
            agent_llm_delay = v['duration_ns'] / 1e6 # ms
            _temp_dict['agent_name'] = agent_name
            _temp_dict['agent-llm_delay'] = agent_llm_delay
            # == Agent-LLM Delay ==

        elif "autogen publish output_topic" in v['name'] or "autogen publish group_topic" in v['name']:
            # == Inter-Agent Delay ==
            if 'agent_name' not in _temp_dict:
                continue
            if 'inter-agent_delay' not in _temp_dict:
                _temp_dict['inter-agent_delay'] = v['duration_ns'] / 1e6 # ms
            else:
                _temp_dict['inter-agent_delay'] += v['duration_ns'] / 1e6 # ms
            # == Inter-Agent Delay ==
        elif v['parent_span_id'] is None:
            whole_time = v['duration_ns'] / 1e6 # ms

    if _temp_dict:
        _return.append(_temp_dict)

    all_agent_llm_delay = 0
    all_processing_time = 0
    all_inter_agent_delay = 0
    for _i in _return:
        all_agent_llm_delay += _i['agent-llm_delay']
        all_processing_time += _i['agent-processing_time']
        all_inter_agent_delay += _i['inter-agent_delay']
    other_time = whole_time - (all_agent_llm_delay + all_processing_time + all_inter_agent_delay)
    _return.append({
        'agent_name': 'total',
        'agent-llm_delay': all_agent_llm_delay,
        'agent-processing_time': all_processing_time,
        'inter-agent_delay': all_inter_agent_delay,
        'other_time': other_time,
        'whole_time': whole_time
    })
    return _return

def token_extractor(_trace_dict):
    _return_list = []
    total_input_token = 0
    total_output_token = 0
    for k, v in _trace_dict.items():
        if v['name'] == 'openai.chat':
            _temp_dict = {}
            current_span_id = k
            target_span_id = v['parent_span_id']
            while True:
                if 'invoke_agent' in _trace_dict[current_span_id]['name']:
                    agent_name = _trace_dict[current_span_id]['name'].split()[-1]
                    break
                current_span_id = target_span_id
                target_span_id = _trace_dict[current_span_id]['parent_span_id']
            input_token = v['attributes']['gen_ai.usage.input_tokens']
            output_token = v['attributes']['gen_ai.usage.output_tokens']
            all_token = v['attributes']['llm.usage.total_tokens']
            assert all_token == input_token + output_token
            total_input_token += input_token
            total_output_token += output_token
            _temp_dict['agent_name'] = agent_name
            _temp_dict['input_token'] = input_token
            _temp_dict['output_token'] = output_token
            _temp_dict['all_token'] = all_token
            _return_list.append(_temp_dict)
    _return_list.append({
        'agent_name': 'total',
        'input_token': total_input_token,
        'output_token': total_output_token,
        'all_token': total_input_token + total_output_token
    })
    return _return_list

def cpu_extractor(_cpu_list):
    start_time = 0
    return_list = []
    for _i in _cpu_list:
        data_points = _i['data_points'][0]
        if not start_time:
            start_time = data_points['timestamp']
        delta_time = (data_points['timestamp'] - start_time) / 1e6 # ms
        cpu_utilization = data_points['value'] * 100 # percentage
        return_list.append((delta_time, cpu_utilization))
    return return_list

def memory_extractor(_memory_list):
    start_time = 0
    return_list = []
    for _i in _memory_list:
        data_points = _i['data_points'][0]
        if not start_time:
            start_time = data_points['timestamp']
        delta_time = (data_points['timestamp'] - start_time) / 1e6 # ms
        memory_usage = data_points['value'] / 1024 / 1024 # KB
        return_list.append((delta_time, memory_usage))
    return return_list

def message_size_extractor(_trace_dict):
    _return_list = []
    for k, v in _trace_dict.items():
        if v['name'] == 'openai.chat':
            _temp_dict = {}
            current_span_id = k
            target_span_id = v['parent_span_id']
            while True:
                if 'invoke_agent' in _trace_dict[current_span_id]['name']:
                    agent_name = _trace_dict[current_span_id]['name'].split()[-1]
                    break
                current_span_id = target_span_id
                target_span_id = _trace_dict[current_span_id]['parent_span_id']
            _temp_dict['agent_name'] = agent_name

            input_all = 0
            output_all = 0
            for _k, _v in v['attributes'].items():
                if re.match(r'gen_ai\.prompt\.\d+\.content', _k):
                    input_all += len(_v.encode('utf-8'))
                elif re.match(r'gen_ai\.completion\.\d+\.content', _k):
                    output_all += len(_v.encode('utf-8'))
            _temp_dict['input_message_size'] = input_all
            _temp_dict['output_message_size'] = output_all
            _return_list.append(_temp_dict)
    return _return_list
