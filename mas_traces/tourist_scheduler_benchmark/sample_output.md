# Sample Tourist Scheduling Run

```
$ python main.py --demand-index 1.1
OpenTelemetry trace log: /.../mas_traces/tourist_scheduler_benchmark/logs/run_20250212_101422.log
Loaded 3 guides and 4 tourists

=== Schedule Summary ===
- alice-adventure matched with florence-foodie (09:00) cost $462.00 score=2
- ben-foodie matched with florence-foodie (09:00) cost $462.00 score=1
- cora-fashion matched with milan-fashion (11:00) cost $594.00 score=2

=== Metrics ===
Assignments: 3
Average cost: $506.00
Avg preference score: 1.67
Fill rate: 75%
Trace log written to /.../logs/run_20250212_101422.log
```

Each execution also writes an OpenTelemetry JSONL file to `logs/`. The spans capture the data loading, market adjustment, scheduling, and per-run parameters—handy for exporting into other monitoring systems.

