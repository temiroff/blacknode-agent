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

`planner` is a deprecated compatibility name for `executive`. It emits a
replacement warning and is planned for removal in `1.0.0`.

`memory` stores task, attempt, evaluation, failure, and improvement records.
`executive` groups mission planning and supervision behind one public
component.
