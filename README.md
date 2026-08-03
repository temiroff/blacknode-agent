# blacknode-agent

`blacknode-agent` provides persistent robot-task memory and executive mission orchestration over stable Blacknode capabilities.

## Components

| Component | Default | Purpose |
|---|---:|---|
| `memory` | On | Store tasks, attempts, evaluations, failures, and conservative improvement recommendations |
| `executive` | Off | Plan and supervise missions, select skills, request confirmation, and review outcomes |

`planner` remains a deprecated alias for `executive` until version 1.0.0.

## Use

Install the package, enable the components required by the workflow, and reload packages:

```powershell
blacknode packages install https://github.com/temiroff/blacknode-agent.git
blacknode packages components blacknode-agent
```

The included `robot-memory-improvement-review.json` template demonstrates task creation, episode-memory ingestion, evaluation recording, querying, and improvement review.

Executive logic requests actions through robot, motion, perception, and skill capability contracts. It never bypasses controller authorization, safety supervision, or explicit confirmation.

## Development

```powershell
python -m pytest packages/blacknode-agent/tests
```

See [AGENTS.md](AGENTS.md) for package boundaries and safety rules.
