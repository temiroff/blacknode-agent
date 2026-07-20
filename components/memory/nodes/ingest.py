"""Adapters that index existing Blacknode artifacts without copying them."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .schemas import utc_now
from .store import MemoryStore, content_fingerprint, stable_id


def _workspace_relative(path: Path) -> str:
    try:
        return str(path.relative_to(Path.cwd().resolve())).replace("\\", "/")
    except ValueError:
        return ""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact(
    source_type: str, path: Path, logical_id: str, fingerprint: str,
    metadata: dict[str, Any], *, referenced_paths: list[Path] | None = None,
) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    references = [item.expanduser().resolve() for item in (referenced_paths or []) if item.exists()]
    stats = [item.stat() for item in references if item.exists()]
    own_stat = resolved.stat()
    return {
        "source_type": source_type,
        "normalized_path": str(resolved),
        "workspace_relative_path": _workspace_relative(resolved),
        "logical_id": logical_id,
        "fingerprint": fingerprint,
        "size_bytes": int(own_stat.st_size if resolved.is_file() else 0) + sum(int(item.st_size) for item in stats),
        "modified_at_ns": max([int(own_stat.st_mtime_ns), *[int(item.st_mtime_ns) for item in stats]]),
        "metadata": {
            **metadata,
            "referenced_paths": [str(item) for item in references],
            "workspace_relative_references": [_workspace_relative(item) for item in references],
        },
    }


def _read_json(path: Path, *, kind: str = "") -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"{path.name} does not exist: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    if kind and value.get("kind") != kind:
        raise ValueError(f"{path.name} is not {kind}")
    return value


def resolve_episode(dataset_root: str | Path, dataset_id: str = "", episode_id: str | int = "") -> dict[str, Any]:
    root = Path(str(dataset_root or "").strip()).expanduser().resolve()
    if (root / "dataset.json").is_file():
        dataset_path = root
    elif dataset_id and (root / str(dataset_id) / "dataset.json").is_file():
        dataset_path = root / str(dataset_id)
    else:
        raise FileNotFoundError(f"Blacknode dataset.json was not found under {root}")
    manifest = _read_json(dataset_path / "dataset.json", kind="blacknode.episode-dataset")
    actual_dataset_id = str(manifest.get("dataset_id") or dataset_path.name)
    if dataset_id and str(dataset_id) != actual_dataset_id:
        raise ValueError(f"dataset_id {dataset_id!r} does not match manifest dataset_id {actual_dataset_id!r}")

    candidates = list(manifest.get("episodes") or [])
    if not candidates:
        raise ValueError("dataset has no saved episodes")
    requested = str(episode_id if episode_id != "" else "0").strip()
    selected: dict[str, Any] | None = None
    for entry in candidates:
        index = int(entry.get("episode_index", -1))
        values = {str(index), f"episode-{index:06d}", str(entry.get("episode_id") or ""), Path(str(entry.get("path") or "")).name}
        if requested in values:
            selected = dict(entry)
            break
    if selected is None:
        raise ValueError(f"episode {requested!r} was not found in dataset {actual_dataset_id}")
    index = int(selected["episode_index"])
    episode_path = (dataset_path / str(selected.get("path") or f"episodes/episode-{index:06d}")).resolve()
    if dataset_path not in episode_path.parents:
        raise ValueError("episode path escapes the dataset root")
    episode = _read_json(episode_path / "episode.json", kind="blacknode.episode")
    data_path = episode_path / "data.parquet"
    if not data_path.is_file():
        raise FileNotFoundError(f"episode data.parquet does not exist: {data_path}")
    cameras = sorted((episode_path / "cameras").glob("*.mp4")) if (episode_path / "cameras").is_dir() else []
    references = [dataset_path / "dataset.json", episode_path / "episode.json", data_path, *cameras]
    episode_key = str(episode.get("episode_id") or selected.get("episode_id") or f"{actual_dataset_id}:{index}")
    fingerprint_value = {
        "dataset": manifest,
        "episode": episode,
        "files": [(str(item.relative_to(dataset_path)), item.stat().st_size, item.stat().st_mtime_ns) for item in references],
    }
    return {
        "dataset_path": dataset_path, "dataset": manifest, "dataset_id": actual_dataset_id,
        "episode_path": episode_path, "episode": episode, "episode_index": index,
        "episode_key": episode_key, "references": references,
        "fingerprint": content_fingerprint(fingerprint_value),
    }


def episode_artifacts(resolved: dict[str, Any]) -> list[dict[str, Any]]:
    dataset_path: Path = resolved["dataset_path"]
    episode_path: Path = resolved["episode_path"]
    dataset = resolved["dataset"]
    episode = resolved["episode"]
    episode_refs = [item for item in resolved["references"] if item != dataset_path / "dataset.json"]
    dataset_fp = content_fingerprint(dataset)
    dataset_artifact = _artifact(
        "dataset", dataset_path / "dataset.json", resolved["dataset_id"], dataset_fp,
        {"dataset_id": resolved["dataset_id"], "task": dataset.get("task", ""), "fps": dataset.get("fps")},
        referenced_paths=[dataset_path / "dataset.json"],
    )
    episode_artifact = _artifact(
        "episode", episode_path, resolved["episode_key"], resolved["fingerprint"],
        {
            "dataset_id": resolved["dataset_id"], "episode_id": resolved["episode_key"],
            "episode_index": resolved["episode_index"], "task": episode.get("task") or dataset.get("task", ""),
            "robot_id": ((episode.get("robot") or {}).get("follower_hardware_id") or dataset.get("robot_type") or ""),
            "joint_names": episode.get("joint_names") or (dataset.get("features") or {}).get("joint_names") or [],
            "cameras": episode.get("cameras") or (dataset.get("features") or {}).get("cameras") or {},
            "fps": episode.get("fps") or dataset.get("fps"), "sample_count": episode.get("frames"),
            "completion_state": "saved", "started_at": episode.get("started_at"),
            "ended_at": episode.get("completed_at") or episode.get("saved_at"),
        },
        referenced_paths=episode_refs,
    )
    return [dataset_artifact, episode_artifact]


def inspect_policy_artifact(path: str | Path) -> tuple[dict[str, Any], dict[str, Any]]:
    value = Path(str(path or "").strip()).expanduser().resolve()
    manifest_path = value / "manifest.json" if value.is_dir() else value
    manifest = _read_json(manifest_path, kind="blacknode.policy-artifact")
    root = manifest_path.parent
    model_path = root / str(manifest.get("model_file") or "")
    references = [manifest_path]
    if model_path.is_file():
        references.append(model_path)
    digest = _sha256_file(manifest_path)
    artifact = _artifact(
        "policy_artifact", root, str(manifest.get("version") or manifest.get("step") or digest[:16]), digest,
        {"manifest": manifest, "manifest_path": str(manifest_path)}, referenced_paths=references,
    )
    policy = {
        "policy_id": stable_id("policy", digest), "artifact_path": str(root), "artifact_digest": digest,
        "policy_type": str(manifest.get("policy_type") or ""),
        "version": str(manifest.get("version") or (f"step-{int(manifest['step']):08d}" if manifest.get("step") is not None else digest[:12])),
        "training_dataset": str(manifest.get("training_dataset") or ""),
        "training_step": int(manifest["step"]) if manifest.get("step") is not None else None,
        "created_at": manifest.get("created_at"), "metrics": manifest.get("metrics") or {},
        "metadata": {"source_checkpoint": manifest.get("source_checkpoint", ""), "backend": manifest.get("backend", "")},
    }
    return artifact, policy


def _ns_to_utc(value: Any) -> str | None:
    try:
        return datetime.fromtimestamp(int(value) / 1_000_000_000, tz=timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def inspect_policy_log(path: str | Path, attempt_seed: str) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any], list[str]]:
    log_path = Path(str(path or "").strip()).expanduser().resolve()
    if not log_path.is_file():
        raise FileNotFoundError(f"policy-run JSONL does not exist: {log_path}")
    warnings: list[str] = []
    parsed: list[tuple[int, dict[str, Any]]] = []
    with log_path.open("r", encoding="utf-8") as handle:
        for line_number, raw in enumerate(handle, 1):
            if not raw.strip():
                continue
            try:
                value = json.loads(raw)
                if not isinstance(value, dict):
                    raise ValueError("line is not a JSON object")
                parsed.append((line_number, value))
            except (json.JSONDecodeError, ValueError) as exc:
                warnings.append(f"line {line_number}: {exc}; skipped")
    run_ids = [str(value.get("run_id") or "") for _, value in parsed if value.get("run_id")]
    run_id = run_ids[0] if run_ids else log_path.stem
    if any(value != run_id for value in run_ids):
        warnings.append("policy log contains multiple run_id values; the first was used")
    artifact = _artifact(
        "policy_log", log_path, run_id, _sha256_file(log_path),
        {"run_id": run_id, "valid_lines": len(parsed), "warning_count": len(warnings)}, referenced_paths=[log_path],
    )

    events: list[dict[str, Any]] = []
    inference_count = 0
    command_count = 0
    clamp_count = 0
    inference_ms: list[float] = []
    first_timestamp = None
    last_timestamp = None
    control_map = {
        "start": "policy_started", "started": "policy_started", "stop": "policy_stopped", "stopped": "policy_stopped",
        "arm": "policy_armed", "disarm": "policy_disarmed", "estop": "estop", "takeover": "takeover",
        "reset_estop": "estop_reset", "reset_takeover": "takeover_reset",
    }
    for line_number, value in parsed:
        timestamp = _ns_to_utc(value.get("recorded_at_ns")) or value.get("timestamp")
        first_timestamp = first_timestamp or timestamp
        last_timestamp = timestamp or last_timestamp
        raw_event = str(value.get("event") or "").strip().lower()
        source_key = f"policy-log:{line_number}"
        if raw_event == "inference":
            inference_count += 1
            command_count += int(bool(value.get("commanded")))
            try:
                inference_ms.append(float(value.get("inference_ms")))
            except (TypeError, ValueError):
                pass
            clamps = list(value.get("clamped") or [])
            blocked = str(value.get("blocked_reason") or "").strip()
            if clamps:
                clamp_count += 1
                events.append({
                    "event_id": stable_id("event", attempt_seed, source_key, "safety_clamp"),
                    "source_event_key": source_key, "event_type": "safety_clamp", "timestamp": timestamp,
                    "source": "policy_runtime", "severity": "warning", "summary": f"Safety gate clamped {len(clamps)} target(s)",
                    "payload": {"clamped": clamps, "source_line": line_number, "log_path": str(log_path),
                                "prediction_reference": bool(value.get("prediction")), "gated_command_reference": bool(value.get("action"))},
                    "artifact_source_type": "policy_log",
                })
            elif blocked:
                events.append({
                    "event_id": stable_id("event", attempt_seed, source_key, "safety_gate_blocked"),
                    "source_event_key": source_key, "event_type": "safety_gate_blocked", "timestamp": timestamp,
                    "source": "policy_runtime", "severity": "error", "summary": blocked,
                    "payload": {"source_line": line_number, "log_path": str(log_path)}, "artifact_source_type": "policy_log",
                })
            continue
        if raw_event == "fault":
            error = str(value.get("error") or "policy runtime fault")
            event_type = "source_staleness" if "stale" in error.casefold() else "inference_fault"
            severity = "critical" if value.get("commands_suppressed") else "error"
            summary = error
        elif raw_event in control_map:
            event_type = control_map[raw_event]
            severity = "critical" if raw_event == "estop" else "warning" if raw_event == "takeover" else "info"
            summary = raw_event.replace("_", " ")
        elif raw_event:
            event_type, severity, summary = raw_event, "info", raw_event.replace("_", " ")
        else:
            continue
        events.append({
            "event_id": stable_id("event", attempt_seed, source_key, event_type),
            "source_event_key": source_key, "event_type": event_type, "timestamp": timestamp,
            "source": "policy_runtime", "severity": severity, "summary": summary,
            "payload": {"source_line": line_number, "log_path": str(log_path), "phase": value.get("phase"),
                        "armed": value.get("armed")}, "artifact_source_type": "policy_log",
        })
    summary_payload = {
        "inference_count": inference_count, "command_count": command_count, "clamp_count": clamp_count,
        "mean_inference_ms": sum(inference_ms) / len(inference_ms) if inference_ms else 0.0,
        "first_timestamp": first_timestamp, "last_timestamp": last_timestamp,
        "valid_lines": len(parsed), "malformed_lines": len(warnings),
    }
    events.append({
        "event_id": stable_id("event", attempt_seed, "policy-log:summary", artifact["fingerprint"]),
        "source_event_key": "policy-log:summary", "event_type": "inference_summary", "timestamp": last_timestamp,
        "source": "policy_runtime", "severity": "info", "summary": f"{inference_count} inferences, {command_count} commands, {clamp_count} clamped steps",
        "payload": {**summary_payload, "log_path": str(log_path)}, "artifact_source_type": "policy_log",
    })
    return artifact, events, {"run_id": run_id, **summary_payload}, warnings


def ingest_episode(
    store: MemoryStore, *, task_id: str, dataset_root: str | Path = "", dataset_id: str = "",
    episode_id: str | int = "", policy_run_path: str | Path = "", policy_artifact_path: str | Path = "",
    attempt_role: str = "deployment",
) -> dict[str, Any]:
    if not dataset_root and not policy_run_path:
        raise ValueError("dataset_root or policy_run_path is required")
    resolved = resolve_episode(dataset_root, dataset_id, episode_id) if dataset_root else None
    seed = (
        f"episode:{resolved['dataset_id']}:{resolved['episode_key']}" if resolved
        else f"policy-log:{Path(str(policy_run_path)).expanduser().resolve()}"
    )
    attempt_id = stable_id("attempt", task_id, seed)
    artifacts = episode_artifacts(resolved) if resolved else []
    events: list[dict[str, Any]] = []
    warnings: list[str] = []
    log_summary: dict[str, Any] = {}
    if policy_run_path:
        log_artifact, log_events, log_summary, log_warnings = inspect_policy_log(policy_run_path, attempt_id)
        artifacts.append(log_artifact)
        events.extend(log_events)
        warnings.extend(log_warnings)
    policy = None
    if policy_artifact_path:
        policy_artifact, policy = inspect_policy_artifact(policy_artifact_path)
        artifacts.append(policy_artifact)
    episode = resolved["episode"] if resolved else {}
    attempt = {
        "attempt_id": attempt_id,
        "dataset_id": resolved["dataset_id"] if resolved else "",
        "episode_index": resolved["episode_index"] if resolved else None,
        "policy_run_id": str(log_summary.get("run_id") or ""),
        "started_at": episode.get("started_at") or log_summary.get("first_timestamp"),
        "ended_at": episode.get("completed_at") or episode.get("saved_at") or log_summary.get("last_timestamp"),
        "metadata": {
            "attempt_role": str(attempt_role or "deployment"),
            "episode_id": resolved["episode_key"] if resolved else "",
            "episode_path": str(resolved["episode_path"]) if resolved else "",
            "dataset_path": str(resolved["dataset_path"]) if resolved else "",
            "policy_log_path": str(Path(str(policy_run_path)).expanduser().resolve()) if policy_run_path else "",
            "policy_log_summary": log_summary,
            "ingested_at": utc_now(),
        },
    }
    stored, status, artifact_statuses = store.ingest_bundle(
        task_id=task_id, attempt=attempt, artifacts=artifacts, events=events, policy=policy,
    )
    return {
        "attempt": stored, "attempt_id": stored["attempt_id"], "ingestion_status": status,
        "artifact_statuses": artifact_statuses, "warnings": warnings,
    }
