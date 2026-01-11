# Sample Output

The snippet below captures a trimmed console session (timestamps omitted) from running

```bash
python main.py --question "Plan a day trip in Melbourne with tennis in the afternoon"
```

```
[planner] Initial plan (4 steps): Gather recent info about day trips in Melbourne | Find transit options ... | Verify tennis court availability | Summarize best itinerary
[agent] Agent executed: Gather recent info about day trips in Melbourne -> Searching for fun activities near Melbourne... (3 Tavily hits)
[replan] Revised plan: Find transit options between downtown Melbourne and the recommended suburb | Verify tennis court availability | Summarize best itinerary
[agent] Agent executed: Find transit options between downtown Melbourne and the recommended suburb -> Checked PTV schedule: trains every 20 minutes from Flinders St to Brighton
[agent] Agent executed: Verify tennis court availability -> Located Brighton Beach tennis club w/ online booking links
[replan] Response ready: Here's a compact itinerary that hits Brighton beach, a seafood lunch, and an afternoon court session...
Final response: Here's a compact itinerary that hits Brighton beach, a seafood lunch, and an afternoon court session with booking links and travel times.
```

During the run the agent also wrote `logs/run_20240510_211402.jsonl` and the companion metadata file. Inspect those artifacts for the full LangGraph state at each node.
