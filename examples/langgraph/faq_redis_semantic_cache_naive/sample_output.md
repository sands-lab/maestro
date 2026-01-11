# Sample Benchmark Output

```
$ python main.py --skip-redis
Encoding 12 FAQ entries with all-mpnet-base-v2

=== In-memory cache demo ===
HIT 'Is it possible to get a refund?' -> Refunds are available within 30 days ... (dist=0.189)
HIT 'I want my money back' -> Submit a refund request in account > orders ... (dist=0.236)
MISS 'What are your business hours?'

=== Extended cache demo ===
HIT 'What time do you open?' -> We open our support lines at 9 a.m. Eastern Time every weekday. (dist=0.097)
HIT 'Is there a phone app?' -> The CustomerApp for Android and iOS ... (dist=0.142)
HIT 'How can I change my payment method?' -> Visit account settings > billing ... (dist=0.121)
```

Running without `--skip-redis` continues with the Redis-backed cache load and the `PerfEval` summary:

```
=== Running LLM benchmark ===
Question: How can I get my money back?
Cache HIT dist=0.144, prompt='How do I get a refund?'
...
--- Benchmark Summary ---
Total elapsed time: 8.41s
Cache hits: 8 | Cache misses: 3
Hit rate: 73%
Average cache_hit latency: 12.4 ms
Average cache_miss latency: 38.5 ms
Average llm_call latency: 2150.1 ms
```

Each run now also produces a trace file like `logs/run_20250212_091530.log` that contains OpenTelemetry span dumps for the warm-up, Redis load, and every benchmark query. Tail one of those files if you prefer text logs over console output.

Use this output as a reference when verifying environment setup or making changes to the benchmark script.
