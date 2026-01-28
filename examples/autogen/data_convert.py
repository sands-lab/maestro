import json
import os
def readin_traces(file):
    _list = []
    with open(file, "r") as f:
        lines = f.readlines()
        for line in lines:
            data = json.loads(line)
            _list.append(data)
    return _list


def readin_metrics(file):
    _dict = {}
    with open(file, "r") as f:
        lines = f.readlines()
        for line in lines:
            data = json.loads(line)
            metric_name = data["metric_name"]
            if metric_name not in _dict:
                _dict[metric_name] = [data]
            else:
                _dict[metric_name].append(data)
    return _dict

def _convert_metrics(metrics_dict):
    cpu_utilization_list = metrics_dict['process.cpu.utilization']
    memory_usage_list = metrics_dict['process.memory.usage']
    return_list = []
    for i in range(len(cpu_utilization_list)):
        cpu_utilization_list[i]['metric_name'] = 'process.cpu.usage'
        memory_usage_list[i]['metric_name'] = 'process.memory.usage_bytes'
        return_list.append(cpu_utilization_list[i])
        return_list.append(memory_usage_list[i])
    return return_list

def _check_traces(traces_list):
    return_dict = {}
    for trace in traces_list:
        attributes = trace['attributes']
        for k in attributes.keys():
            if k == 'gen_ai.request.reasoning_effort':
                return_dict[k] = []
                continue
            if k not in return_dict:
                return_dict[k] = [attributes[k]]
            else:
                return_dict[k].append(attributes[k])

        for k in return_dict.keys():
            try:
                return_dict[k] = list(set(return_dict[k]))
            except Exception:
                print(k)
    return return_dict

def _convert_traces(traces_list):
    for trace in traces_list:
        attributes: dict = trace['attributes']
        attributes['gen_ai.mcp.count'] = 0
        attributes['gen_ai.llm.call.count'] = 0
        if trace['name'] == 'openai.chat':
            attributes['gen_ai.operation.name'] = 'call_llm'
            attributes['gen_ai.llm.call.count'] = 1
            attributes['gen_ai.usage.total_tokens'] = attributes.pop('llm.usage.total_tokens', 0)

        if 'gen_ai.operation.name' in attributes and attributes['gen_ai.operation.name'] == 'execute_tool':
            if 'tool.name' in attributes:
                attributes['gen_ai.tool.type'] = 'FunctionTool'
            else:
                attributes['gen_ai.tool.type'] = 'Builtin'

        if 'gen_ai.completion.0.tool_calls.0.name' in attributes:
            attributes['gen_ai.operation.name'] = 'execute_tool'
            attributes['gen_ai.tool.type'] = 'Builtin'
            attributes['gen_ai.tool.name'] = attributes.pop('gen_ai.completion.0.tool_calls.0.name', '')

        if 'communication' in trace:
            communication = trace['communication']
            attributes['communication.input_message_size_bytes']=communication.get('input_message_size_bytes', 0)
            attributes['communication.output_message_size_bytes']=communication.get('output_message_size_bytes', 0)
            attributes['communication.total_message_size_bytes']=communication.get('total_message_size_bytes', 0)
            trace.pop('communication', None)

    return traces_list


def convert_metrics(dir):
    for root, dirs, files in os.walk(dir + "/metrics"):
        for file in files:
            metrics_dict = readin_metrics(os.path.join(root, file))
            converted_metrics = _convert_metrics(metrics_dict)
            if not os.path.exists(dir + "/converted_metric"):
                os.makedirs(dir + "/converted_metric")

            with open(os.path.join(dir + "/converted_metric", file), "w") as f:
                for metric in converted_metrics:
                    f.write(json.dumps(metric) + "\n")

def convert_traces(dir, is_check=False):
    for root, dirs, files in os.walk(dir + "/traces"):
        for file in files:
            traces_list = readin_traces(os.path.join(root, file))
            if is_check:
                check_result = _check_traces(traces_list)
                print(json.dumps(check_result, indent=4))
                exit(0)
            else:
                converted_traces = _convert_traces(traces_list)
                if not os.path.exists(dir + "/converted_traces"):
                    os.makedirs(dir + "/converted_traces")

                with open(os.path.join(dir + "/converted_traces", file), "w") as f:
                    for trace in converted_traces:
                        f.write(json.dumps(trace) + "\n")

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--dir', type=str)
    parser.add_argument('--is_check', action='store_true')
    args = parser.parse_args()
    # If is_check is True, the program will exit after checking the traces
    convert_traces(args.dir, is_check=args.is_check)
    convert_metrics(args.dir)
