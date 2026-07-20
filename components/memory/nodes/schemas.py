"""Stable typed contracts for Blacknode robot memory."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

OUTCOMES = {"unknown", "success", "failure", "partial", "interrupted", "unsafe"}
SUBTASK_STATUSES = {"unknown", "not_started", "in_progress", "success", "failure", "skipped"}
TASK_STATUSES = {"active", "paused", "complete", "archived"}
RECOMMENDATIONS = {
    "continue", "retry", "review_evaluation", "collect_correction",
    "collect_more_demonstrations", "retrain_candidate",
    "run_simulation_evaluation", "stop_and_review",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def canonical_subtask_key(name: str) -> str:
    value = "_".join(str(name or "").strip().lower().split())
    return "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in value).strip("_")


def _confidence(value: Any, *, field_name: str = "confidence") -> float | None:
    if value is None or value == "":
        return None
    parsed = float(value)
    if not 0.0 <= parsed <= 1.0:
        raise ValueError(f"{field_name} must be between 0 and 1")
    return parsed


@dataclass(frozen=True)
class TaskEvaluation:
    attempt_id: str
    outcome: str
    success: bool | None
    confidence: float | None
    summary: str = ""
    failure_type: str = ""
    subtasks: list[dict[str, Any]] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)
    evaluator: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    task_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Recommendation:
    recommended_action: str
    reason: str
    priority: str
    requires_human_review: bool
    supporting_attempt_ids: list[str] = field(default_factory=list)
    rule_version: str = "phase1-v1"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def validate_evaluation(value: dict[str, Any], *, attempt_id: str = "") -> TaskEvaluation:
    if not isinstance(value, dict):
        raise ValueError("evaluation must be a dictionary")
    resolved_attempt = str(attempt_id or value.get("attempt_id") or "").strip()
    if not resolved_attempt:
        raise ValueError("evaluation requires attempt_id")
    supplied_attempt = str(value.get("attempt_id") or "").strip()
    if attempt_id and supplied_attempt and supplied_attempt != resolved_attempt:
        raise ValueError("evaluation attempt_id does not match the node attempt_id")

    outcome = str(value.get("outcome") or "unknown").strip().lower()
    if outcome not in OUTCOMES:
        raise ValueError(f"unsupported outcome: {outcome}")
    raw_success = value.get("success")
    if raw_success is not None and not isinstance(raw_success, bool):
        raise ValueError("success must be true, false, or null")
    success = raw_success
    if outcome == "success" and success is not True:
        raise ValueError("outcome=success requires success=true")
    if success is True and outcome != "success":
        raise ValueError("success=true requires outcome=success")
    if outcome in {"failure", "unsafe"} and success is not False:
        raise ValueError(f"outcome={outcome} requires success=false")

    confidence = _confidence(value.get("confidence"))
    evaluator = value.get("evaluator") or {}
    if not isinstance(evaluator, dict):
        raise ValueError("evaluator must be a dictionary")
    evaluator_type = str(evaluator.get("type") or "").strip()
    if not evaluator_type:
        raise ValueError("evaluator.type is required")

    subtasks: list[dict[str, Any]] = []
    raw_subtasks = value.get("subtasks") or []
    if not isinstance(raw_subtasks, list):
        raise ValueError("subtasks must be a list")
    for index, raw in enumerate(raw_subtasks):
        if not isinstance(raw, dict):
            raise ValueError(f"subtasks[{index}] must be a dictionary")
        name = str(raw.get("name") or "").strip()
        if not name:
            raise ValueError(f"subtasks[{index}].name is required")
        status = str(raw.get("status") or "unknown").strip().lower()
        if status not in SUBTASK_STATUSES:
            raise ValueError(f"unsupported subtask status: {status}")
        start_frame = raw.get("start_frame")
        end_frame = raw.get("end_frame")
        if start_frame is not None and int(start_frame) < 0:
            raise ValueError("start_frame must be non-negative")
        if end_frame is not None and int(end_frame) < 0:
            raise ValueError("end_frame must be non-negative")
        if start_frame is not None and end_frame is not None and int(end_frame) < int(start_frame):
            raise ValueError("end_frame must be greater than or equal to start_frame")
        subtasks.append({
            **raw,
            "name": name,
            "subtask_key": str(raw.get("subtask_key") or canonical_subtask_key(name)),
            "sequence_index": int(raw.get("sequence_index", index)),
            "status": status,
            "confidence": _confidence(raw.get("confidence"), field_name=f"subtasks[{index}].confidence"),
            "start_frame": int(start_frame) if start_frame is not None else None,
            "end_frame": int(end_frame) if end_frame is not None else None,
        })

    evidence = value.get("evidence") or {}
    metadata = value.get("metadata") or {}
    if not isinstance(evidence, dict) or not isinstance(metadata, dict):
        raise ValueError("evidence and metadata must be dictionaries")
    return TaskEvaluation(
        task_id=str(value.get("task_id") or "").strip(),
        attempt_id=resolved_attempt,
        outcome=outcome,
        success=success,
        confidence=confidence,
        failure_type=str(value.get("failure_type") or "").strip(),
        summary=str(value.get("summary") or "").strip(),
        subtasks=subtasks,
        evidence=evidence,
        evaluator={**evaluator, "type": evaluator_type},
        metadata=metadata,
    )
