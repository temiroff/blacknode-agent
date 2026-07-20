"""Deterministic Phase 1 robot-memory contracts."""
from __future__ import annotations

import base64
import html
import json
import re
from pathlib import Path

import pytest

import blacknode  # noqa: F401 - triggers extension-package discovery
from blacknode.node import _NODE_REGISTRY
from blacknode.pkg.blacknode_agent.memory import MemoryStore, ingest_episode, query_task_history, recommend_next_action, validate_evaluation
from blacknode.pkg.blacknode_agent.memory import memory_nodes
from blacknode.workflow import validate_workflow


EXPECTED_NODES = {
    "RobotTaskCreate", "EpisodeMemoryIngest", "TaskEvaluationRecord",
    "RobotMemoryQuery", "AdaptationRecommendation",
}


def test_dashboard_wraps_complete_text_and_expands_height() -> None:
    long_reason = (
        "The latest shifted-cube attempt produced a repeated locate-green-cube failure, "
        "so collect a corrected demonstration before creating a candidate policy."
    )
    long_path = r"C:\Users\temir\.blacknode\datasets\teleoperation-demo\episodes\episode-000000\cameras\camera_0.mp4"
    reason_lines = memory_nodes._wrapped_lines(long_reason, 32)
    path_lines = memory_nodes._wrapped_lines(long_path, 24)
    assert " ".join(reason_lines) == " ".join(long_reason.split())
    assert "".join(path_lines) == long_path

    data_url = memory_nodes._dashboard(
        "ROBOT MEMORY WITH A COMPLETE LONG TITLE THAT MUST WRAP",
        "collect correction after repeated shifted cube failure",
        [("Reason", long_reason), ("Artifact path", long_path), *[(f"Detail {index}", long_reason) for index in range(7)]],
    )
    prefix = "data:image/svg+xml;base64,"
    svg = base64.b64decode(data_url[len(prefix):]).decode("utf-8")
    height = int(re.search(r'<svg[^>]+height="(\d+)"', svg).group(1))
    assert height > 350
    assert "…" not in svg
    for line in [*memory_nodes._wrapped_lines(long_reason, 56), *memory_nodes._wrapped_lines(long_path, 56)]:
        assert html.escape(line) in svg


def _write_dataset(root: Path, count: int = 1) -> Path:
    dataset = root / "cube-memory"
    entries = []
    for index in range(count):
        episode_id = f"episode-test-{index}"
        relative = f"episodes/episode-{index:06d}"
        episode_path = dataset / relative
        (episode_path / "cameras").mkdir(parents=True, exist_ok=True)
        (episode_path / "data.parquet").write_bytes(f"synthetic-parquet-{index}".encode())
        (episode_path / "cameras/front.mp4").write_bytes(f"synthetic-video-{index}".encode())
        episode = {
            "kind": "blacknode.episode", "schema_version": 1, "episode_id": episode_id,
            "run_id": f"record-{index}", "episode_index": index,
            "started_at": f"2026-07-19T00:0{index}:00Z", "completed_at": f"2026-07-19T00:0{index}:10Z",
            "task": "pick up green cube", "fps": 10, "frames": 100,
            "joint_names": ["shoulder", "gripper"], "robot": {"follower_hardware_id": "arm-1"},
            "cameras": {"front": {"frames": 100}}, "saved_at": f"2026-07-19T00:0{index}:10Z",
        }
        (episode_path / "episode.json").write_text(json.dumps(episode), encoding="utf-8")
        entries.append({
            "episode_id": episode_id, "episode_index": index, "path": relative,
            "frames": 100, "duration_seconds": 10.0, "task": "pick up green cube",
            "saved_at": episode["saved_at"],
        })
    manifest = {
        "kind": "blacknode.episode-dataset", "schema_version": 1, "dataset_id": "cube-memory",
        "created_at": "2026-07-19T00:00:00Z", "updated_at": "2026-07-19T00:10:00Z",
        "fps": 10, "task": "pick up green cube", "robot_type": "test-arm",
        "features": {"joint_names": ["shoulder", "gripper"], "cameras": {"front": {}}},
        "episodes": entries,
    }
    (dataset / "dataset.json").write_text(json.dumps(manifest), encoding="utf-8")
    return dataset


def _write_log(path: Path, run_id: str, *, estop: bool = False, malformed: bool = False) -> Path:
    lines = [
        {"recorded_at_ns": 1_752_883_200_000_000_000, "run_id": run_id, "event": "arm", "phase": "running", "armed": True},
        {"recorded_at_ns": 1_752_883_201_000_000_000, "run_id": run_id, "event": "inference", "phase": "running",
         "armed": True, "commanded": True, "inference_ms": 12.5, "prediction": [0.1, 0.2],
         "action": {"shoulder": 0.1}, "clamped": ["gripper:velocity"], "blocked_reason": ""},
    ]
    if estop:
        lines.append({"recorded_at_ns": 1_752_883_202_000_000_000, "run_id": run_id, "event": "estop", "phase": "emergency_stopped", "armed": False})
    content = "\n".join(json.dumps(item) for item in lines)
    if malformed:
        content += "\n{malformed optional line\n"
    path.write_text(content + "\n", encoding="utf-8")
    return path


def _evaluation(attempt_id: str, *, success: bool, failure_type: str = "", subtask: str = "") -> dict:
    outcome = "success" if success else "failure"
    subtasks = [
        {"name": "locate green cube", "sequence_index": 0, "status": "failure" if subtask else "success",
         "confidence": 0.95, "failure_type": failure_type}
    ] if subtask else []
    return {
        "attempt_id": attempt_id, "outcome": outcome, "success": success, "confidence": 0.95,
        "failure_type": failure_type, "summary": "completed" if success else "cube moved left and was not located",
        "subtasks": subtasks, "evidence": {"camera": "front"},
        "evaluator": {"type": "human", "name": "operator"},
    }


def test_nodes_registered_and_safe_defaults(tmp_path: Path) -> None:
    for name in EXPECTED_NODES:
        assert name in _NODE_REGISTRY
        assert _NODE_REGISTRY[name]._bn_package == "blacknode-agent"
    assert _NODE_REGISTRY["RobotTaskCreate"]._bn_input_defaults["action"] == "check"
    assert _NODE_REGISTRY["EpisodeMemoryIngest"]._bn_input_defaults["action"] == "check"
    assert _NODE_REGISTRY["TaskEvaluationRecord"]._bn_input_defaults["action"] == "check"
    database = tmp_path / "memory.db"
    result = _NODE_REGISTRY["RobotTaskCreate"]({
        "memory_path": str(database), "action": "check", "task_name": "pick up green cube",
        "task_description": "pick and place", "robot_id": "arm-1", "environment": "real",
    })
    assert result["status"] == "ready"
    assert result["task_id"].startswith("task-")
    assert not database.exists()
    query = _NODE_REGISTRY["RobotMemoryQuery"]({"memory_path": str(database), "task_id": result["task_id"]})
    assert query["count"] == 0
    assert not database.exists()


def test_task_persists_after_reopening_database(tmp_path: Path) -> None:
    path = tmp_path / "memory.db"
    with MemoryStore(path) as store:
        task = store.create_task(task_name="pick up green cube", robot_id="arm-1", environment="real")
        assert store.schema_version == 1
    with MemoryStore(path) as reopened:
        assert reopened.get_task(task["task_id"])["task_name"] == "pick up green cube"


def test_visual_node_chain_records_and_reviews_one_attempt(tmp_path: Path) -> None:
    dataset = _write_dataset(tmp_path)
    database = tmp_path / "memory.db"
    task = _NODE_REGISTRY["RobotTaskCreate"]({
        "memory_path": str(database), "action": "create", "task_name": "pick up green cube",
        "task_description": "place at demonstrated target", "robot_id": "arm-1", "environment": "real",
    })
    assert task["status"] == "created_or_existing"
    ingested = _NODE_REGISTRY["EpisodeMemoryIngest"]({
        "memory_path": str(database), "task_id": task["task_id"], "dataset_root": str(dataset),
        "episode_id": "episode-test-0", "attempt_role": "deployment", "action": "ingest",
    })
    assert ingested["ingestion_status"] == "created"
    evaluation = _NODE_REGISTRY["TaskEvaluationRecord"]({
        "memory_path": str(database), "attempt_id": ingested["attempt_id"],
        "evaluation": _evaluation(ingested["attempt_id"], success=True), "action": "record",
    })
    assert evaluation["evaluation_status"] == "created"
    queried = _NODE_REGISTRY["RobotMemoryQuery"]({
        "memory_path": str(database), "task_id": task["task_id"], "query_type": "task_history", "limit": 20,
    })
    assert queried["count"] == 1 and queried["summary"]["success_rate"] == 1.0
    recommendation = _NODE_REGISTRY["AdaptationRecommendation"]({
        "memory_path": str(database), "action": "record", "task": task["task"],
        "latest_attempt": queried["summary"]["latest_attempt"], "memory_summary": queried["summary"],
    })
    assert recommendation["recommended_action"] == "continue"
    assert recommendation["recommendation_status"] == "recorded_or_existing"
    with MemoryStore(database) as store:
        assert store.connection.execute("SELECT COUNT(*) FROM recommendations").fetchone()[0] == 1
    assert all(item["dashboard"].startswith("data:image/svg+xml;base64,") for item in (task, ingested, evaluation, queried, recommendation))


def test_ingestion_is_idempotent_references_artifacts_and_skips_malformed_lines(tmp_path: Path) -> None:
    dataset = _write_dataset(tmp_path)
    log = _write_log(tmp_path / "policy-run.jsonl", "run-one", malformed=True)
    database = tmp_path / "memory" / "memory.db"
    with MemoryStore(database) as store:
        task = store.create_task(task_name="pick up green cube", robot_id="arm-1", environment="real")
        first = ingest_episode(store, task_id=task["task_id"], dataset_root=dataset, episode_id="episode-test-0", policy_run_path=log)
        second = ingest_episode(store, task_id=task["task_id"], dataset_root=dataset, episode_id="0", policy_run_path=log)
        assert first["ingestion_status"] == "created"
        assert second["ingestion_status"] == "unchanged"
        assert first["attempt_id"] == second["attempt_id"]
        assert first["warnings"] and "line 3" in first["warnings"][0]
        with log.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "recorded_at_ns": 1_752_883_203_000_000_000, "run_id": "run-one", "event": "inference",
                "phase": "running", "armed": True, "commanded": False, "inference_ms": 10.0,
                "prediction": [0.2, 0.3], "action": {}, "clamped": [], "blocked_reason": "",
            }) + "\n")
        updated = ingest_episode(store, task_id=task["task_id"], dataset_root=dataset, episode_id="0", policy_run_path=log)
        assert updated["ingestion_status"] == "updated"
        assert store.connection.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == 1
        assert store.connection.execute("SELECT COUNT(*) FROM memory_events WHERE event_type='safety_clamp'").fetchone()[0] == 1
        summary_payload = json.loads(store.connection.execute(
            "SELECT payload_json FROM memory_events WHERE event_type='inference_summary'"
        ).fetchone()[0])
        assert summary_payload["inference_count"] == 2
        artifacts = [dict(row) for row in store.connection.execute("SELECT * FROM artifacts ORDER BY source_type")]
        referenced = json.loads(next(item for item in artifacts if item["source_type"] == "episode")["metadata_json"])["referenced_paths"]
        assert str(dataset / "episodes/episode-000000/data.parquet") in referenced
        assert str(dataset / "episodes/episode-000000/cameras/front.mp4") in referenced
    assert not (database.parent / "data.parquet").exists()
    assert not (database.parent / "front.mp4").exists()


def test_evaluations_are_validated_immutable_and_queryable(tmp_path: Path) -> None:
    dataset = _write_dataset(tmp_path, count=2)
    database = tmp_path / "memory.db"
    with MemoryStore(database) as store:
        task = store.create_task(task_name="pick up green cube")
        first = ingest_episode(store, task_id=task["task_id"], dataset_root=dataset, episode_id="0")
        second = ingest_episode(store, task_id=task["task_id"], dataset_root=dataset, episode_id="1")
        _, created = store.record_evaluation(validate_evaluation(_evaluation(first["attempt_id"], success=True)))
        failure = _evaluation(second["attempt_id"], success=False, failure_type="scene_shift", subtask="locate green cube")
        _, failed_created = store.record_evaluation(validate_evaluation(failure))
        _, unchanged = store.record_evaluation(validate_evaluation(failure))
        assert (created, failed_created, unchanged) == ("created", "created", "unchanged")
        assert store.connection.execute("SELECT COUNT(*) FROM evaluations").fetchone()[0] == 2
        history = query_task_history(store, task_id=task["task_id"], query_type="recent_failures")
        assert history["count"] == 1
        assert history["summary"]["success_rate"] == 0.5
        failures = store.query(task_id=task["task_id"], query_type="subtask_failure_counts")
        assert failures == [{"subtask_key": "locate_green_cube", "name": "locate green cube", "failure_type": "scene_shift", "count": 1, "latest_at": failures[0]["latest_at"]}]
    with pytest.raises(ValueError, match="outcome=success"):
        validate_evaluation({"attempt_id": "a", "outcome": "success", "success": False, "evaluator": {"type": "human"}})


def test_same_scene_then_moved_cube_is_remembered_and_recommends_retry(tmp_path: Path) -> None:
    dataset = _write_dataset(tmp_path, count=3)
    with MemoryStore(tmp_path / "memory.db") as store:
        task = store.create_task(task_name="pick up green cube", task_description="place at demonstrated target")
        exact = ingest_episode(store, task_id=task["task_id"], dataset_root=dataset, episode_id="0")
        moved = ingest_episode(store, task_id=task["task_id"], dataset_root=dataset, episode_id="1")
        store.record_evaluation(validate_evaluation(_evaluation(exact["attempt_id"], success=True)))
        store.record_evaluation(validate_evaluation(_evaluation(moved["attempt_id"], success=False, failure_type="scene_shift", subtask="locate green cube")))
        summary = store.task_summary(task["task_id"])
        recommendation = recommend_next_action(task=task, latest_attempt=store.get_attempt(moved["attempt_id"]), memory_summary=summary)
        assert recommendation["recommended_action"] == "retry"
        assert summary["subtask_failures"][0]["failure_type"] == "scene_shift"

        moved_again = ingest_episode(store, task_id=task["task_id"], dataset_root=dataset, episode_id="2")
        store.record_evaluation(validate_evaluation(_evaluation(moved_again["attempt_id"], success=False, failure_type="scene_shift", subtask="locate green cube")))
        repeated_summary = store.task_summary(task["task_id"])
        repeated = recommend_next_action(task=task, latest_attempt=store.get_attempt(moved_again["attempt_id"]), memory_summary=repeated_summary)
        assert repeated["recommended_action"] == "collect_correction"


def test_estop_produces_stop_and_review_before_an_evaluation(tmp_path: Path) -> None:
    dataset = _write_dataset(tmp_path)
    log = _write_log(tmp_path / "unsafe.jsonl", "unsafe-run", estop=True)
    with MemoryStore(tmp_path / "memory.db") as store:
        task = store.create_task(task_name="pick up green cube")
        result = ingest_episode(store, task_id=task["task_id"], dataset_root=dataset, episode_id="0", policy_run_path=log)
        summary = store.task_summary(task["task_id"])
        recommendation = recommend_next_action(task=task, latest_attempt=store.get_attempt(result["attempt_id"]), memory_summary=summary)
        assert summary["serious_safety_event_count"] == 1
        assert recommendation["recommended_action"] == "stop_and_review"


def test_failed_ingestion_preserves_transactional_integrity(tmp_path: Path) -> None:
    dataset = _write_dataset(tmp_path)
    with MemoryStore(tmp_path / "memory.db") as store:
        task = store.create_task(task_name="pick up green cube")
        with pytest.raises(FileNotFoundError):
            ingest_episode(store, task_id=task["task_id"], dataset_root=dataset, episode_id="0", policy_artifact_path=tmp_path / "missing-policy")
        assert store.connection.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == 0
        assert store.connection.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0] == 0


def test_template_validates() -> None:
    path = Path(__file__).resolve().parents[1] / "components/memory/templates/robot-memory-improvement-review.json"
    result = validate_workflow(json.loads(path.read_text(encoding="utf-8")))
    assert result.ok, result.errors
