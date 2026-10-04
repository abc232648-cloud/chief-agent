# Worker

The worker is the execution layer behind the dashboard command center.

Run it with:

```bash
python -m worker.runner
```

Flow:

`Dashboard → SQLite command queue → Qwen/Mistral gateway → structured plan → Policy Gate → safe worker actions / approval queue`

The worker never treats AI output as authority. Forbidden actions are blocked and high-impact actions require explicit approval.
