"""Typed visual nodes for persistent Blacknode robot memory."""
from __future__ import annotations

import base64
import html
import textwrap
from pathlib import Path
from typing import Any

from blacknode.node import Any as AnyPort
from blacknode.node import Bool, Dict, Enum, Float, Image, Int, List, Text, node

from .ingest import ingest_episode, inspect_policy_artifact, inspect_policy_log, resolve_episode
from .queries import query_task_history
from .recommendations import recommend_next_action
from .schemas import OUTCOMES, validate_evaluation
from .store import DEFAULT_MEMORY_PATH, MemoryStore, content_fingerprint, stable_id

_CATEGORY = "Robot Memory"


def _memory_path(value: Any) -> Path:
    return Path(str(value or DEFAULT_MEMORY_PATH)).expanduser().resolve()


def _wrapped_lines(value: Any, width: int = 56) -> list[str]:
    """Wrap complete dashboard text, including long paths, without truncation."""
    lines: list[str] = []
    for raw_line in str(value).splitlines() or [""]:
        lines.extend(textwrap.wrap(
            raw_line, width=max(8, int(width)), break_long_words=True,
            break_on_hyphens=False, replace_whitespace=False, drop_whitespace=True,
        ) or [""])
    return lines


def _svg_lines(lines: list[str], *, x: int, y: int, fill: str, size: int, line_height: int, weight: int = 400) -> str:
    tspans = "".join(
        f'<tspan x="{x}" dy="{0 if index == 0 else line_height}">{html.escape(line)}</tspan>'
        for index, line in enumerate(lines)
    )
    return f'<text x="{x}" y="{y}" fill="{fill}" font-family="sans-serif" font-size="{size}" font-weight="{weight}">{tspans}</text>'


def _dashboard(title: str, state: str, details: list[tuple[str, Any]], *, warning: bool = False) -> str:
    color = "#f59e0b" if warning else "#14b8a6"
    title_lines = _wrapped_lines(title, 50)
    state_lines = _wrapped_lines(state.upper(), 64)
    state_y = 84 + max(0, len(title_lines) - 1) * 26
    rows: list[str] = []
    y = state_y + max(0, len(state_lines) - 1) * 22 + 34
    for label, value in details:
        value_lines = _wrapped_lines(value, 56)
        rows.append(_svg_lines([str(label).upper()], x=34, y=y, fill="#94a3b8", size=13, line_height=18))
        rows.append(_svg_lines(value_lines, x=250, y=y, fill="#f8fafc", size=14, line_height=20))
        y += max(34, len(value_lines) * 20 + 14)
    height = max(240, y + 52)
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="760" height="{height}" viewBox="0 0 760 {height}">'
        f'<rect width="760" height="{height}" rx="20" fill="#0b1020"/>'
        f'<circle cx="38" cy="42" r="10" fill="{color}"/>'
        + _svg_lines(title_lines, x=58, y=50, fill="#f8fafc", size=22, line_height=26, weight=800)
        + _svg_lines(state_lines, x=34, y=state_y, fill=color, size=16, line_height=22, weight=700)
        + "".join(rows)
        + f'<text x="726" y="{height - 20}" text-anchor="end" fill="#64748b" font-family="sans-serif" font-size="12">Persistent evidence · advisory actions · motion remains disarmed</text></svg>'
    )
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode("utf-8")).decode("ascii")


@node(
    name="RobotTaskCreate", category=_CATEGORY,
    description="Check, create, or retrieve a persistent robot task. Defaults to a read-only check.",
    inputs={
        "memory_path": Text(default=str(DEFAULT_MEMORY_PATH)), "action": Enum(["check", "create", "get"], default="check"),
        "task_id": Text(default=""), "task_name": Text(default=""), "task_description": Text(default=""),
        "robot_id": Text(default=""), "environment": Text(default="real"), "metadata": Dict(default={}),
    },
    outputs={"task_id": Text, "task": Dict, "status": Text, "dashboard": Image, "report": Text},
    primary_inputs=[], primary_outputs=["task", "status", "dashboard"],
)
def robot_task_create(ctx: dict) -> dict:
    action = str(ctx.get("action") or "check").lower()
    path = _memory_path(ctx.get("memory_path"))
    try:
        if action == "check":
            name = str(ctx.get("task_name") or "").strip()
            task_id = str(ctx.get("task_id") or "").strip()
            existing = None
            if path.is_file():
                with MemoryStore(path) as store:
                    existing = store.get_task(task_id) if task_id else store.find_task(
                        task_name=name, task_description=str(ctx.get("task_description") or ""),
                        robot_id=str(ctx.get("robot_id") or ""), environment=str(ctx.get("environment") or ""),
                    ) if name else None
            status = "existing" if existing else "ready"
            report = "task exists" if existing else "ready to create; set action=create explicitly"
            if not task_id and name:
                stable_key = content_fingerprint({
                    "task_name": name.casefold(), "task_description": str(ctx.get("task_description") or "").strip(),
                    "robot_id": str(ctx.get("robot_id") or "").strip(),
                    "environment": str(ctx.get("environment") or "").strip(),
                })
                task_id = stable_id("task", stable_key)
            task = existing or {
                "task_id": task_id, "task_name": name, "task_description": str(ctx.get("task_description") or ""),
                "robot_id": str(ctx.get("robot_id") or ""), "environment": str(ctx.get("environment") or ""),
            }
        elif action == "get":
            if not path.is_file():
                raise FileNotFoundError(f"memory database does not exist: {path}")
            with MemoryStore(path) as store:
                task = store.get_task(str(ctx.get("task_id") or "").strip())
            if task is None:
                raise ValueError("task was not found")
            status, report = "existing", "task retrieved"
        elif action == "create":
            with MemoryStore(path) as store:
                task = store.create_task(
                    task_id=str(ctx.get("task_id") or ""), task_name=str(ctx.get("task_name") or ""),
                    task_description=str(ctx.get("task_description") or ""), robot_id=str(ctx.get("robot_id") or ""),
                    environment=str(ctx.get("environment") or ""), metadata=dict(ctx.get("metadata") or {}),
                )
            status, report = "created_or_existing", "persistent task ready"
        else:
            raise ValueError(f"unsupported action: {action}")
        dashboard = _dashboard("ROBOT TASK MEMORY", status, [
            ("Task", task.get("task_name", "")), ("Robot", task.get("robot_id", "")),
            ("Environment", task.get("environment", "")), ("Memory", path),
        ])
        return {"task_id": str(task.get("task_id") or ""), "task": task, "status": status, "dashboard": dashboard, "report": report}
    except Exception as exc:  # noqa: BLE001
        report = f"task {action} failed: {type(exc).__name__}: {exc}"
        return {"task_id": "", "task": {}, "status": "failed", "dashboard": _dashboard("ROBOT TASK MEMORY", "failed", [("Error", report)], warning=True), "report": report}


def _check_attempt(ctx: dict) -> dict[str, Any]:
    task_id = str(ctx.get("task_id") or "").strip()
    dataset_root = str(ctx.get("dataset_root") or "").strip()
    policy_run_path = str(ctx.get("policy_run_path") or "").strip()
    if not task_id:
        raise ValueError("task_id is required")
    if not dataset_root and not policy_run_path:
        raise ValueError("dataset_root or policy_run_path is required")
    resolved = resolve_episode(dataset_root, str(ctx.get("dataset_id") or ""), str(ctx.get("episode_id") or "0")) if dataset_root else None
    seed = f"episode:{resolved['dataset_id']}:{resolved['episode_key']}" if resolved else f"policy-log:{Path(policy_run_path).expanduser().resolve()}"
    attempt_id = stable_id("attempt", task_id, seed)
    warnings: list[str] = []
    summary: dict[str, Any] = {}
    if policy_run_path:
        _, _, summary, warnings = inspect_policy_log(policy_run_path, attempt_id)
    if str(ctx.get("policy_artifact_path") or "").strip():
        inspect_policy_artifact(str(ctx.get("policy_artifact_path")))
    return {
        "attempt_id": attempt_id, "dataset_id": resolved["dataset_id"] if resolved else "",
        "episode_index": resolved["episode_index"] if resolved else None,
        "episode_path": str(resolved["episode_path"]) if resolved else "", "policy_log_summary": summary,
        "warnings": warnings,
    }


@node(
    name="EpisodeMemoryIngest", category=_CATEGORY,
    description="Check or index one existing Blacknode episode and optional policy-run log. References source artifacts and never controls motion or starts training.",
    inputs={
        "memory_path": Text(default=str(DEFAULT_MEMORY_PATH)), "task_id": Text(default=""),
        "dataset_root": Text(default=""), "dataset_id": Text(default=""), "episode_id": Text(default="0"),
        "attempt_role": Enum(["demonstration", "deployment", "replay", "simulation", "correction"], default="deployment"),
        "policy_run_path": Text(default=""), "policy_artifact_path": Text(default=""),
        "action": Enum(["check", "ingest", "status"], default="check"),
    },
    outputs={"attempt_id": Text, "ingestion_status": Text, "attempt": Dict, "warnings": List, "dashboard": Image, "report": Text},
    primary_inputs=["task_id"], primary_outputs=["attempt", "ingestion_status", "dashboard"],
)
def episode_memory_ingest(ctx: dict) -> dict:
    action = str(ctx.get("action") or "check").lower()
    try:
        checked = _check_attempt(ctx)
        if action == "check":
            result = {"attempt_id": checked["attempt_id"], "ingestion_status": "ready", "attempt": checked, "warnings": checked["warnings"]}
            report = "artifacts validated; set action=ingest explicitly"
        elif action == "status":
            path = _memory_path(ctx.get("memory_path"))
            if not path.is_file():
                raise FileNotFoundError(f"memory database does not exist: {path}")
            with MemoryStore(path) as store:
                attempt = store.get_attempt(checked["attempt_id"])
            result = {"attempt_id": checked["attempt_id"], "ingestion_status": "existing" if attempt else "missing", "attempt": attempt or checked, "warnings": checked["warnings"]}
            report = "attempt retrieved" if attempt else "attempt has not been ingested"
        elif action == "ingest":
            with MemoryStore(_memory_path(ctx.get("memory_path"))) as store:
                result = ingest_episode(
                    store, task_id=str(ctx.get("task_id") or ""), dataset_root=str(ctx.get("dataset_root") or ""),
                    dataset_id=str(ctx.get("dataset_id") or ""), episode_id=str(ctx.get("episode_id") or "0"),
                    attempt_role=str(ctx.get("attempt_role") or "deployment"),
                    policy_run_path=str(ctx.get("policy_run_path") or ""), policy_artifact_path=str(ctx.get("policy_artifact_path") or ""),
                )
            report = f"attempt {result['ingestion_status']}; {len(result['warnings'])} warning(s)"
        else:
            raise ValueError(f"unsupported action: {action}")
        attempt = result["attempt"]
        dashboard = _dashboard("EPISODE MEMORY", result["ingestion_status"], [
            ("Attempt", result["attempt_id"]), ("Dataset", attempt.get("dataset_id", checked.get("dataset_id", ""))),
            ("Episode", attempt.get("episode_index", checked.get("episode_index", ""))),
            ("Policy run", attempt.get("policy_run_id", "")), ("Warnings", len(result["warnings"])),
        ], warning=bool(result["warnings"]))
        return {**result, "dashboard": dashboard, "report": report}
    except Exception as exc:  # noqa: BLE001
        report = f"episode {action} failed: {type(exc).__name__}: {exc}"
        return {"attempt_id": "", "ingestion_status": "failed", "attempt": {}, "warnings": [report],
                "dashboard": _dashboard("EPISODE MEMORY", "failed", [("Error", report)], warning=True), "report": report}


@node(
    name="TaskEvaluationRecord", category=_CATEGORY,
    description="Validate or append a human, deterministic, simulation, VLM, or external evaluator result. Defaults to check and preserves evaluator history.",
    inputs={
        "memory_path": Text(default=str(DEFAULT_MEMORY_PATH)), "attempt_id": Text(default=""),
        "evaluation": Dict(default={}), "action": Enum(["check", "record", "status"], default="check"),
    },
    outputs={
        "evaluation_status": Text, "success": Bool, "outcome": Text, "confidence": Float,
        "failed_subtask": Text, "evaluation": Dict, "dashboard": Image, "report": Text,
    },
    primary_inputs=["attempt_id", "evaluation"], primary_outputs=["evaluation", "evaluation_status", "dashboard"],
)
def task_evaluation_record(ctx: dict) -> dict:
    action = str(ctx.get("action") or "check").lower()
    try:
        if action == "status":
            path = _memory_path(ctx.get("memory_path"))
            if not path.is_file():
                raise FileNotFoundError(f"memory database does not exist: {path}")
            with MemoryStore(path) as store:
                rows = store.connection.execute(
                    "SELECT * FROM evaluations WHERE attempt_id=? ORDER BY created_at DESC, evaluation_id DESC LIMIT 1",
                    (str(ctx.get("attempt_id") or ""),),
                ).fetchall()
            if not rows:
                raise ValueError("attempt has no recorded evaluation")
            raw = dict(rows[0])
            evaluation = {"attempt_id": raw["attempt_id"], "outcome": raw["outcome"], "success": None if raw["success"] is None else bool(raw["success"]),
                          "confidence": raw["confidence"], "failure_type": raw["failure_type"], "summary": raw["summary"]}
            status = "existing"
        else:
            validated = validate_evaluation(dict(ctx.get("evaluation") or {}), attempt_id=str(ctx.get("attempt_id") or ""))
            evaluation = validated.to_dict()
            if action == "check":
                status = "ready"
            elif action == "record":
                with MemoryStore(_memory_path(ctx.get("memory_path"))) as store:
                    evaluation, status = store.record_evaluation(validated)
            else:
                raise ValueError(f"unsupported action: {action}")
        failures = [item for item in evaluation.get("subtasks", []) if item.get("status") == "failure"]
        failed_subtask = str((failures[0] if failures else {}).get("name") or "")
        report = f"evaluation {status}: {evaluation.get('outcome', 'unknown')}"
        dashboard = _dashboard("TASK EVALUATION", status, [
            ("Outcome", evaluation.get("outcome", "unknown")), ("Success", evaluation.get("success")),
            ("Confidence", evaluation.get("confidence")), ("Failed subtask", failed_subtask or "none"),
            ("Evaluator", (evaluation.get("evaluator") or {}).get("type", "stored")),
        ], warning=evaluation.get("outcome") in {"failure", "unsafe"})
        return {"evaluation_status": status, "success": bool(evaluation.get("success")), "outcome": str(evaluation.get("outcome") or "unknown"),
                "confidence": float(evaluation.get("confidence") or 0.0), "failed_subtask": failed_subtask,
                "evaluation": evaluation, "dashboard": dashboard, "report": report}
    except Exception as exc:  # noqa: BLE001
        report = f"evaluation {action} failed: {type(exc).__name__}: {exc}"
        return {"evaluation_status": "failed", "success": False, "outcome": "unknown", "confidence": 0.0,
                "failed_subtask": "", "evaluation": {}, "dashboard": _dashboard("TASK EVALUATION", "failed", [("Error", report)], warning=True), "report": report}


@node(
    name="RobotMemoryQuery", category=_CATEGORY,
    description="Retrieve deterministic structured task history, failures, successful attempts, policy performance, or latest state.",
    inputs={
        "trigger": AnyPort(default=None), "memory_path": Text(default=str(DEFAULT_MEMORY_PATH)), "task_id": Text(default=""),
        "query_type": Enum(["task_history", "recent_attempts", "recent_failures", "subtask_failure_counts", "successful_attempts", "policy_performance", "latest_task_state"], default="task_history"),
        "limit": Int(default=20), "policy_version": Text(default=""),
        "outcome": Enum(["", *sorted(OUTCOMES)], default=""), "failure_type": Text(default=""),
    },
    outputs={"results": List, "summary": Dict, "count": Int, "dashboard": Image, "report": Text},
    primary_inputs=["task_id"], primary_outputs=["results", "summary", "dashboard"],
)
def robot_memory_query(ctx: dict) -> dict:
    try:
        path = _memory_path(ctx.get("memory_path"))
        if not path.is_file():
            raise FileNotFoundError(f"memory database does not exist: {path}")
        with MemoryStore(path) as store:
            value = query_task_history(
                store, task_id=str(ctx.get("task_id") or ""), query_type=str(ctx.get("query_type") or "task_history"),
                limit=int(ctx.get("limit") or 20), policy_version=str(ctx.get("policy_version") or ""),
                outcome=str(ctx.get("outcome") or ""), failure_type=str(ctx.get("failure_type") or ""),
            )
        summary = value["summary"]
        evaluated = int(summary.get("evaluated_attempt_count") or 0)
        success_text = f"{summary['success_rate']:.0%}" if evaluated else "not evaluated"
        report = f"{value['count']} result(s); {summary['attempt_count']} attempt(s); success {success_text}"
        dashboard = _dashboard("ROBOT MEMORY", "ready", [
            ("Task", (summary.get("task") or {}).get("task_name", "")), ("Attempts", summary.get("attempt_count", 0)),
            ("Success rate", success_text), ("Safety events", summary.get("safety_event_count", 0)),
            ("Top failed subtask", ((summary.get("subtask_failures") or [{}])[0]).get("name", "none")),
        ], warning=bool(summary.get("safety_event_count")))
        return {**value, "dashboard": dashboard, "report": report}
    except Exception as exc:  # noqa: BLE001
        report = f"memory query failed: {type(exc).__name__}: {exc}"
        return {"results": [], "summary": {}, "count": 0, "dashboard": _dashboard("ROBOT MEMORY", "failed", [("Error", report)], warning=True), "report": report}


@node(
    name="AdaptationRecommendation", category=_CATEGORY,
    description="Produce a conservative advisory next step from evaluated memory. It never trains, deploys, arms, or changes a policy.",
    inputs={
        "memory_path": Text(default=str(DEFAULT_MEMORY_PATH)),
        "action": Enum(["check", "record"], default="check"),
        "task": Dict(default={}), "latest_attempt": Dict(default={}), "memory_summary": Dict(default={}),
        "minimum_confidence": Float(default=0.7), "repeated_failure_threshold": Int(default=2),
        "new_correction_threshold": Int(default=3),
    },
    outputs={
        "recommended_action": Text, "reason": Text, "priority": Text,
        "requires_human_review": Bool, "supporting_attempt_ids": List, "recommendation": Dict,
        "recommendation_status": Text, "dashboard": Image,
    },
    primary_inputs=["task", "latest_attempt", "memory_summary"], primary_outputs=["recommendation", "recommended_action", "dashboard"],
)
def adaptation_recommendation(ctx: dict) -> dict:
    value = recommend_next_action(
        task=dict(ctx.get("task") or {}), latest_attempt=dict(ctx.get("latest_attempt") or {}),
        memory_summary=dict(ctx.get("memory_summary") or {}), minimum_confidence=float(ctx.get("minimum_confidence") or 0.7),
        repeated_failure_threshold=int(ctx.get("repeated_failure_threshold") or 2),
        new_correction_threshold=int(ctx.get("new_correction_threshold") or 3),
    )
    status = "ready"
    if str(ctx.get("action") or "check").lower() == "record":
        task = dict(ctx.get("task") or {})
        latest = dict(ctx.get("latest_attempt") or {})
        task_id = str(task.get("task_id") or "")
        if not task_id:
            raise ValueError("task.task_id is required to record a recommendation")
        path = _memory_path(ctx.get("memory_path"))
        if not path.is_file():
            raise FileNotFoundError(f"memory database does not exist: {path}")
        with MemoryStore(path) as store:
            stored = store.record_recommendation(
                task_id=task_id, attempt_id=str(latest.get("attempt_id") or ""), recommendation=value,
                input_value={
                    "task": task, "latest_attempt": latest, "memory_summary": dict(ctx.get("memory_summary") or {}),
                    "minimum_confidence": float(ctx.get("minimum_confidence") or 0.7),
                    "repeated_failure_threshold": int(ctx.get("repeated_failure_threshold") or 2),
                    "new_correction_threshold": int(ctx.get("new_correction_threshold") or 3),
                },
            )
        value = {**value, "recommendation_id": stored.get("recommendation_id", "")}
        status = "recorded_or_existing"
    dashboard = _dashboard("IMPROVEMENT REVIEW", value["recommended_action"], [
        ("Priority", value["priority"]), ("Human review", value["requires_human_review"]),
        ("Reason", value["reason"]), ("Evidence", ", ".join(value["supporting_attempt_ids"]) or "none"),
    ], warning=value["priority"] in {"high", "critical"})
    return {**value, "recommendation": value, "recommendation_status": status, "dashboard": dashboard}
