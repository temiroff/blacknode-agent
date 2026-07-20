"""Public deterministic query helpers."""
from __future__ import annotations

from typing import Any

from .store import MemoryStore


def query_task_history(
    store: MemoryStore, *, task_id: str, query_type: str = "task_history", limit: int = 20,
    policy_version: str = "", outcome: str = "", failure_type: str = "",
) -> dict[str, Any]:
    results = store.query(
        task_id=task_id, query_type=query_type, limit=limit, policy_version=policy_version,
        outcome=outcome, failure_type=failure_type,
    )
    summary = store.task_summary(task_id)
    return {"results": results, "summary": summary, "count": len(results)}
