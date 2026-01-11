import json
from pathlib import Path
from extractor import (
    token_extractor,
    delay_extractor,
    cpu_extractor,
    memory_extractor,
    message_size_extractor,
)


def readin_traces(file):
    _dict = {}
    with open(file, "r") as f:
        lines = f.readlines()
        for line in lines:
            data = json.loads(line)
            span_id = data["span_id"]
            _dict[span_id] = data
    return _dict


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


def token_latest(dir):
    files = Path(dir).glob("*.jsonl")
    if files:
        latest_file = max(files, key=lambda f: f.name)
        _trace_dict = readin_traces(latest_file)
        return token_extractor(_trace_dict)
    else:
        print("No jsonl files found in the directory.")
        return []


def token_per_run(dir):
    files = Path(dir).glob("*.jsonl")
    files = sorted(files, key=lambda f: f.name)
    _return_list = []
    for i, file in enumerate(files):
        _trace_dict = readin_traces(file)
        get_token_result = token_extractor(_trace_dict)
        _return_list.append({i + 1: get_token_result[-1]})
    return _return_list


def delay_breakdown_latest(dir):
    files = Path(dir).glob("*.jsonl")
    if files:
        latest_file = max(files, key=lambda f: f.name)
        _trace_dict = readin_traces(latest_file)
        return delay_extractor(_trace_dict)
    else:
        print("No jsonl files found in the directory.")
        return []


def delay_breakdown_per_run(dir):
    files = Path(dir).glob("*.jsonl")
    files = sorted(files, key=lambda f: f.name)
    _return_list = []
    for i, file in enumerate(files):
        _trace_dict = readin_traces(file)
        get_delay_breakdown_result = delay_extractor(_trace_dict)
        _return_list.append({i + 1: get_delay_breakdown_result[-1]})
    return _return_list


def cpu_usage_latest(dir):
    files = Path(dir).glob("*.jsonl")
    if files:
        latest_file = max(files, key=lambda f: f.name)
        _metric_dict = readin_metrics(latest_file)
        return cpu_extractor(_metric_dict["process.cpu.utilization"])
    else:
        print("No jsonl files found in the directory.")
        return []


def cpu_usage_per_run(dir):
    files = Path(dir).glob("*.jsonl")
    files = sorted(files, key=lambda f: f.name)
    _return_list = []
    for i, file in enumerate(files):
        _metric_dict = readin_metrics(file)
        get_cpu_usage_result = cpu_extractor(_metric_dict["process.cpu.utilization"])
        _return_list.append({i + 1: get_cpu_usage_result})
    return _return_list


def memory_usage_latest(dir):
    files = Path(dir).glob("*.jsonl")
    if files:
        latest_file = max(files, key=lambda f: f.name)
        _metric_dict = readin_metrics(latest_file)
        return memory_extractor(_metric_dict["process.memory.usage"])
    else:
        print("No jsonl files found in the directory.")
        return []


def memory_usage_per_run(dir):
    files = Path(dir).glob("*.jsonl")
    files = sorted(files, key=lambda f: f.name)
    _return_list = []
    for i, file in enumerate(files):
        _metric_dict = readin_metrics(file)
        get_memory_usage_result = memory_extractor(_metric_dict["process.memory.usage"])
        _return_list.append({i + 1: get_memory_usage_result})
    return _return_list


def message_size_latest(dir):
    files = Path(dir).glob("*.jsonl")
    if files:
        latest_file = max(files, key=lambda f: f.name)
        _trace_dict = readin_traces(latest_file)
        return message_size_extractor(_trace_dict)
    else:
        print("No jsonl files found in the directory.")
        return []


def message_size_per_run(dir):
    files = Path(dir).glob("*.jsonl")
    files = sorted(files, key=lambda f: f.name)
    _return_list = []
    for i, file in enumerate(files):
        _trace_dict = readin_traces(file)
        get_message_size_result = message_size_extractor(_trace_dict)
        _return_list.append({i + 1: get_message_size_result})
    return _return_list


def print_data(name, data):
    with open(name, "w") as f:
        json.dump(data, f, indent=4)
    print(f"{name} saved.")


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", type=str, required=True, help="Path to the directory")
    args = parser.parse_args()
    # == Analyze Traces ==
    traces_path = args.dir + "/traces"
    result_path = args.dir + "/results"
    Path(result_path).mkdir(parents=True, exist_ok=True)
    latest_token_per_agent = token_latest(traces_path)
    print_data(result_path + "/latest_token_per_agent.json", latest_token_per_agent)
    token_per_runs = token_per_run(traces_path)
    print_data(result_path + "/token_per_runs.json", token_per_runs)
    latest_delay_breakdown = delay_breakdown_latest(traces_path)
    print_data(result_path + "/delay_breakdown_latest.json", latest_delay_breakdown)
    delay_breakdown_per_runs = delay_breakdown_per_run(traces_path)
    print_data(result_path + "/delay_breakdown_per_runs.json", delay_breakdown_per_runs)
    message_size_latest_result = message_size_latest(traces_path)
    print_data(result_path + "/message_size_latest.json", message_size_latest_result)
    message_size_per_runs = message_size_per_run(traces_path)
    print_data(result_path + "/message_size_per_runs.json", message_size_per_runs)

    # == Analyze Metrics ==
    metrics_path = args.dir + "/metrics"
    latest_cpu_usage = cpu_usage_latest(metrics_path)
    print_data(result_path + "/latest_cpu_usage.json", latest_cpu_usage)
    latest_memory_usage = memory_usage_latest(metrics_path)
    print_data(result_path + "/latest_memory_usage.json", latest_memory_usage)
    cpu_usage_per_runs = cpu_usage_per_run(metrics_path)
    print_data(result_path + "/cpu_usage_per_runs.json", cpu_usage_per_runs)
    memory_usage_per_runs = memory_usage_per_run(metrics_path)
    print_data(result_path + "/memory_usage_per_runs.json", memory_usage_per_runs)
    latest_cpu_usage = cpu_usage_latest(metrics_path)
    print_data(result_path + '/latest_cpu_usage.json', latest_cpu_usage)
    latest_memory_usage = memory_usage_latest(metrics_path)
    print_data(result_path + '/latest_memory_usage.json', latest_memory_usage)
    cpu_usage_per_runs = cpu_usage_per_run(metrics_path)
    print_data(result_path + '/cpu_usage_per_runs.json', cpu_usage_per_runs)
    memory_usage_per_runs = memory_usage_per_run(metrics_path)
    print_data(result_path + '/memory_usage_per_runs.json', memory_usage_per_runs)


if __name__ == "__main__":
    main()
