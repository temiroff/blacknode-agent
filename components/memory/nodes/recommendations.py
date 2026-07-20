"""Conservative deterministic improvement recommendations."""
from __future__ import annotations

from typing import Any

from .schemas import Recommendation


def recommend_next_action(
    *, task: dict[str, Any] | None = None, latest_attempt: dict[str, Any] | None = None,
    memory_summary: dict[str, Any] | None = None, minimum_confidence: float = 0.7,
    repeated_failure_threshold: int = 2, new_correction_threshold: int = 3,
) -> dict[str, Any]:
    task = dict(task or {})
    latest = dict(latest_attempt or {})
    summary = dict(memory_summary or {})
    summarized_latest = dict(summary.get("latest_attempt") or {})
    if summarized_latest and (not latest or str(latest.get("outcome") or "unknown") == "unknown"):
        latest = summarized_latest
    attempt_id = str(latest.get("attempt_id") or "")
    supporting = [attempt_id] if attempt_id else []
    outcome = str(latest.get("outcome") or "unknown")
    confidence = latest.get("confidence")
    safety_events = int(summary.get("safety_event_count") or 0)
    serious_safety_events = int(summary.get("serious_safety_event_count") or 0)
    unsafe_attempts = int(summary.get("unsafe_attempt_count") or 0)
    events = list(latest.get("events") or [])

    serious_event = next((event for event in events if event.get("severity") in {"error", "critical"}
                          or event.get("event_type") in {"estop", "source_staleness"}), None)
    if outcome == "unsafe" or unsafe_attempts or serious_safety_events or serious_event:
        reason = "An unsafe outcome or serious runtime event requires inspection before another attempt."
        return Recommendation("stop_and_review", reason, "critical", True, supporting).to_dict()
    if confidence is None or outcome == "unknown":
        return Recommendation(
            "review_evaluation", "The latest attempt has no trusted outcome evaluation.", "high", True, supporting
        ).to_dict()
    if float(confidence) < float(minimum_confidence):
        return Recommendation(
            "review_evaluation", f"Evaluator confidence {float(confidence):.2f} is below {float(minimum_confidence):.2f}.",
            "high", True, supporting,
        ).to_dict()
    if latest.get("success") is True or outcome == "success":
        return Recommendation("continue", "The latest evaluated attempt succeeded.", "normal", False, supporting).to_dict()

    correction_count = int(summary.get("new_correction_count") or 0)
    if correction_count >= int(new_correction_threshold):
        return Recommendation(
            "retrain_candidate", f"{correction_count} reviewed corrections are ready for an offline candidate policy.",
            "normal", True, supporting,
        ).to_dict()
    repeated = [
        item for item in (summary.get("subtask_failures") or [])
        if int(item.get("count") or 0) >= int(repeated_failure_threshold)
    ]
    if repeated:
        top = repeated[0]
        return Recommendation(
            "collect_correction",
            f"Subtask '{top.get('name') or top.get('subtask_key')}' failed {int(top.get('count') or 0)} times.",
            "high", True, supporting,
        ).to_dict()
    failure_attempts = int(summary.get("failure_attempt_count") or 0)
    if outcome in {"failure", "partial", "interrupted"} and failure_attempts <= 1:
        return Recommendation("retry", "The first ordinary failure should be repeated before changing the policy.", "normal", False, supporting).to_dict()
    if outcome in {"failure", "partial", "interrupted"}:
        return Recommendation(
            "collect_more_demonstrations", "Failures are present but do not yet identify one repeated correction target.",
            "normal", True, supporting,
        ).to_dict()
    if safety_events:
        return Recommendation("stop_and_review", "Safety-related evidence requires review.", "high", True, supporting).to_dict()
    return Recommendation("continue", f"Task '{task.get('task_name') or task.get('task_id') or 'task'}' has no blocking evidence.", "normal", False, supporting).to_dict()
