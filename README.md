# blacknode-agent

This repository is the AI orchestration layer over stable Blacknode capability
contracts.

Its public component model is:

```text
blacknode-agent
├── memory
└── executive
    ├── task_planner
    ├── mission_runner
    ├── skill_registry
    ├── confirmation
    └── mission_review
```

`memory` stores task, attempt, evaluation, failure, and improvement records.
`executive` groups mission planning and supervision behind one public
component.
