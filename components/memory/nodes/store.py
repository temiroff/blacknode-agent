"""Durable SQLite storage, independent of Blacknode node classes."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any, Iterable, Protocol

from .migrations import MIGRATIONS
from .schemas import TaskEvaluation, utc_now

DEFAULT_MEMORY_PATH = Path(".blacknode") / "memory" / "memory.db"
_ID_NAMESPACE = uuid.UUID("f6ad3d8e-5d59-4c31-81d9-89ce4bb8c38a")
_JSON_COLUMNS = {
    "metadata_json": "metadata", "metrics_json": "metrics", "evidence_json": "evidence",
    "payload_json": "payload", "supporting_attempt_ids_json": "supporting_attempt_ids",
}


class MemoryBackend(Protocol):
    def create_task(self, **values: Any) -> dict[str, Any]: ...
    def get_task(self, task_id: str) -> dict[str, Any] | None: ...
    def close(self) -> None: ...


def stable_id(prefix: str, *parts: Any) -> str:
    canonical = "\x1f".join(str(part or "").strip() for part in parts)
    return f"{prefix}-{uuid.uuid5(_ID_NAMESPACE, canonical).hex[:20]}"


def content_fingerprint(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, sort_keys=True, separators=(",", ":"), default=str)


def _decode_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    result = dict(row)
    for column, name in _JSON_COLUMNS.items():
        if column in result:
            raw = result.pop(column)
            try:
                result[name] = json.loads(raw or ("[]" if name == "supporting_attempt_ids" else "{}"))
            except json.JSONDecodeError:
                result[name] = [] if name == "supporting_attempt_ids" else {}
    for name in ("success", "requires_human_review"):
        if name in result and result[name] is not None:
            result[name] = bool(result[name])
    return result


class MemoryStore:
    """SQLite-backed persistent robot memory.

    Open one instance per API/node operation. Transactions cover complete
    ingestion and evaluation writes, while WAL and a busy timeout support
    concurrent editor reads.
    """

    def __init__(self, path: str | Path = DEFAULT_MEMORY_PATH) -> None:
        value = Path(path or DEFAULT_MEMORY_PATH).expanduser()
        self.path = value.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, timeout=5.0)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA busy_timeout=5000")
        try:
            self.connection.execute("PRAGMA journal_mode=WAL")
        except sqlite3.DatabaseError:
            pass
        self._migrate()

    def __enter__(self) -> "MemoryStore":
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()

    def close(self) -> None:
        self.connection.close()

    def _migrate(self) -> None:
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        applied = {int(row[0]) for row in self.connection.execute("SELECT version FROM schema_migrations")}
        for version, sql in MIGRATIONS:
            if version in applied:
                continue
            with self.connection:
                self.connection.executescript(sql)
                self.connection.execute(
                    "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                    (version, utc_now()),
                )
                self.connection.execute(f"PRAGMA user_version={int(version)}")

    @property
    def schema_version(self) -> int:
        return int(self.connection.execute("PRAGMA user_version").fetchone()[0])

    def create_task(
        self, *, task_name: str, task_description: str = "", robot_id: str = "",
        environment: str = "", metadata: dict[str, Any] | None = None,
        task_id: str = "", status: str = "active",
    ) -> dict[str, Any]:
        name = str(task_name or "").strip()
        if not name:
            raise ValueError("task_name is required")
        stable_key = content_fingerprint({
            "task_name": name.casefold(), "task_description": str(task_description or "").strip(),
            "robot_id": str(robot_id or "").strip(), "environment": str(environment or "").strip(),
        })
        resolved_id = str(task_id or "").strip() or stable_id("task", stable_key)
        existing_id = self.get_task(resolved_id)
        if existing_id is not None and existing_id.get("stable_key") != stable_key:
            raise ValueError(f"task_id {resolved_id!r} already identifies a different task")
        now = utc_now()
        with self.connection:
            self.connection.execute(
                """INSERT OR IGNORE INTO tasks
                (task_id, stable_key, task_name, task_description, robot_id, environment, status, metadata_json, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (resolved_id, stable_key, name, str(task_description or "").strip(), str(robot_id or "").strip(),
                 str(environment or "").strip(), status, _json(metadata or {}), now, now),
            )
        task = self.get_task(resolved_id)
        if task is None:
            task = _decode_row(self.connection.execute("SELECT * FROM tasks WHERE stable_key=?", (stable_key,)).fetchone())
        assert task is not None
        return task

    def find_task(self, *, task_name: str, task_description: str = "", robot_id: str = "", environment: str = "") -> dict[str, Any] | None:
        stable_key = content_fingerprint({
            "task_name": str(task_name or "").strip().casefold(),
            "task_description": str(task_description or "").strip(),
            "robot_id": str(robot_id or "").strip(), "environment": str(environment or "").strip(),
        })
        return _decode_row(self.connection.execute("SELECT * FROM tasks WHERE stable_key=?", (stable_key,)).fetchone())

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        return _decode_row(self.connection.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone())

    def get_attempt(self, attempt_id: str) -> dict[str, Any] | None:
        return _decode_row(self.connection.execute("SELECT * FROM attempts WHERE attempt_id=?", (attempt_id,)).fetchone())

    def upsert_artifact(self, artifact: dict[str, Any], *, cursor: sqlite3.Cursor | None = None) -> tuple[dict[str, Any], str]:
        executor = cursor or self.connection
        existing = executor.execute(
            "SELECT * FROM artifacts WHERE source_type=? AND normalized_path=? AND logical_id=?",
            (artifact["source_type"], artifact["normalized_path"], artifact.get("logical_id", "")),
        ).fetchone()
        status = "created" if existing is None else "unchanged" if existing["fingerprint"] == artifact["fingerprint"] else "updated"
        artifact_id = existing["artifact_id"] if existing else stable_id(
            "artifact", artifact["source_type"], artifact["normalized_path"], artifact.get("logical_id", "")
        )
        executor.execute(
            """INSERT INTO artifacts
            (artifact_id, source_type, normalized_path, workspace_relative_path, logical_id, fingerprint,
             size_bytes, modified_at_ns, metadata_json, ingested_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(source_type, normalized_path, logical_id) DO UPDATE SET
             fingerprint=excluded.fingerprint, size_bytes=excluded.size_bytes,
             modified_at_ns=excluded.modified_at_ns, metadata_json=excluded.metadata_json,
             workspace_relative_path=excluded.workspace_relative_path, ingested_at=excluded.ingested_at""",
            (artifact_id, artifact["source_type"], artifact["normalized_path"], artifact.get("workspace_relative_path", ""),
             artifact.get("logical_id", ""), artifact["fingerprint"], int(artifact.get("size_bytes") or 0),
             int(artifact.get("modified_at_ns") or 0), _json(artifact.get("metadata") or {}), utc_now()),
        )
        row = executor.execute("SELECT * FROM artifacts WHERE artifact_id=?", (artifact_id,)).fetchone()
        return _decode_row(row) or {}, status

    def ingest_bundle(
        self, *, task_id: str, attempt: dict[str, Any], artifacts: list[dict[str, Any]],
        events: list[dict[str, Any]], policy: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], str, dict[str, str]]:
        if self.get_task(task_id) is None:
            raise ValueError(f"task does not exist: {task_id}")
        artifact_statuses: dict[str, str] = {}
        artifact_rows: dict[str, dict[str, Any]] = {}
        now = utc_now()
        with self.connection:
            cursor = self.connection.cursor()
            for source in artifacts:
                row, status = self.upsert_artifact(source, cursor=cursor)
                artifact_rows[source["source_type"]] = row
                artifact_statuses[source["source_type"]] = status

            policy_id = None
            if policy:
                policy_artifact = artifact_rows.get("policy_artifact")
                policy_id = policy["policy_id"]
                cursor.execute(
                    """INSERT INTO policies
                    (policy_id, artifact_id, artifact_path, artifact_digest, policy_type, version, training_dataset,
                     training_step, created_at, metrics_json, metadata_json)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(policy_id) DO UPDATE SET metrics_json=excluded.metrics_json, metadata_json=excluded.metadata_json""",
                    (policy_id, policy_artifact.get("artifact_id") if policy_artifact else None, policy["artifact_path"],
                     policy["artifact_digest"], policy.get("policy_type", ""), policy.get("version", ""),
                     policy.get("training_dataset", ""), policy.get("training_step"), policy.get("created_at"),
                     _json(policy.get("metrics") or {}), _json(policy.get("metadata") or {})),
                )

            existing = None
            if attempt.get("dataset_id") and attempt.get("episode_index") is not None:
                existing = cursor.execute(
                    "SELECT * FROM attempts WHERE task_id=? AND dataset_id=? AND episode_index=?",
                    (task_id, attempt["dataset_id"], int(attempt["episode_index"])),
                ).fetchone()
            if existing is None and attempt.get("policy_run_id"):
                existing = cursor.execute(
                    "SELECT * FROM attempts WHERE task_id=? AND policy_run_id=?",
                    (task_id, attempt["policy_run_id"]),
                ).fetchone()
            attempt_id = existing["attempt_id"] if existing else attempt["attempt_id"]
            before = _decode_row(existing)
            metadata = dict((before or {}).get("metadata") or {})
            metadata.update(attempt.get("metadata") or {})
            cursor.execute(
                """INSERT INTO attempts
                (attempt_id, task_id, episode_artifact_id, policy_log_artifact_id, dataset_id, episode_index,
                 policy_run_id, policy_id, started_at, ended_at, outcome, success, confidence, failure_type,
                 summary, metadata_json, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(attempt_id) DO UPDATE SET
                 episode_artifact_id=COALESCE(excluded.episode_artifact_id, attempts.episode_artifact_id),
                 policy_log_artifact_id=COALESCE(excluded.policy_log_artifact_id, attempts.policy_log_artifact_id),
                 policy_run_id=CASE WHEN excluded.policy_run_id<>'' THEN excluded.policy_run_id ELSE attempts.policy_run_id END,
                 policy_id=COALESCE(excluded.policy_id, attempts.policy_id), started_at=COALESCE(excluded.started_at, attempts.started_at),
                 ended_at=COALESCE(excluded.ended_at, attempts.ended_at), metadata_json=excluded.metadata_json, updated_at=excluded.updated_at""",
                (attempt_id, task_id, (artifact_rows.get("episode") or {}).get("artifact_id"),
                 (artifact_rows.get("policy_log") or {}).get("artifact_id"), attempt.get("dataset_id", ""),
                 attempt.get("episode_index"), attempt.get("policy_run_id", ""), policy_id,
                 attempt.get("started_at"), attempt.get("ended_at"), (before or {}).get("outcome", "unknown"),
                 None if (before or {}).get("success") is None else int(bool((before or {}).get("success"))),
                 (before or {}).get("confidence"), (before or {}).get("failure_type", ""),
                 (before or {}).get("summary", ""), _json(metadata), (before or {}).get("created_at", now), now),
            )
            for event in events:
                artifact_row = artifact_rows.get(event.get("artifact_source_type", ""))
                cursor.execute(
                    """INSERT INTO memory_events
                    (event_id, attempt_id, artifact_id, source_event_key, event_type, timestamp, frame_index,
                     source, severity, summary, payload_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(attempt_id, source_event_key) DO UPDATE SET
                     artifact_id=excluded.artifact_id, event_type=excluded.event_type, timestamp=excluded.timestamp,
                     frame_index=excluded.frame_index, source=excluded.source, severity=excluded.severity,
                     summary=excluded.summary, payload_json=excluded.payload_json""",
                    (event["event_id"], attempt_id, artifact_row.get("artifact_id") if artifact_row else None,
                     event["source_event_key"], event["event_type"], event.get("timestamp"), event.get("frame_index"),
                     event.get("source", ""), event.get("severity", "info"), event.get("summary", ""),
                     _json(event.get("payload") or {})),
                )
        row = self.get_attempt(attempt_id)
        assert row is not None
        all_unchanged = existing is not None and all(value == "unchanged" for value in artifact_statuses.values())
        return row, "unchanged" if all_unchanged else "updated" if existing is not None else "created", artifact_statuses

    def record_evaluation(self, evaluation: TaskEvaluation) -> tuple[dict[str, Any], str]:
        attempt = self.get_attempt(evaluation.attempt_id)
        if attempt is None:
            raise ValueError(f"attempt does not exist: {evaluation.attempt_id}")
        if evaluation.task_id and evaluation.task_id != attempt["task_id"]:
            raise ValueError("evaluation task_id does not match the attempt task")
        payload = evaluation.to_dict()
        fingerprint = content_fingerprint(payload)
        evaluation_id = stable_id("evaluation", evaluation.attempt_id, fingerprint)
        evaluator = evaluation.evaluator
        now = utc_now()
        existing = self.connection.execute(
            "SELECT * FROM evaluations WHERE attempt_id=? AND content_fingerprint=?",
            (evaluation.attempt_id, fingerprint),
        ).fetchone()
        status = "unchanged" if existing else "created"
        with self.connection:
            self.connection.execute(
                """INSERT OR IGNORE INTO evaluations
                (evaluation_id, attempt_id, evaluator_type, evaluator_name, evaluator_version, outcome, success,
                 confidence, failure_type, summary, evidence_json, metadata_json, content_fingerprint, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (evaluation_id, evaluation.attempt_id, evaluator["type"], str(evaluator.get("name") or ""),
                 str(evaluator.get("version") or ""), evaluation.outcome,
                 None if evaluation.success is None else int(evaluation.success), evaluation.confidence,
                 evaluation.failure_type, evaluation.summary, _json(evaluation.evidence), _json(evaluation.metadata),
                 fingerprint, now),
            )
            if existing is None:
                for subtask in evaluation.subtasks:
                    subtask_id = stable_id("subtask", evaluation_id, subtask["sequence_index"], subtask["subtask_key"])
                    self.connection.execute(
                        """INSERT INTO subtask_results
                        (subtask_result_id, evaluation_id, attempt_id, subtask_key, name, sequence_index, status,
                         confidence, start_timestamp, end_timestamp, start_frame, end_frame, failure_type,
                         summary, evidence_json, metadata_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (subtask_id, evaluation_id, evaluation.attempt_id, subtask["subtask_key"], subtask["name"],
                         subtask["sequence_index"], subtask["status"], subtask.get("confidence"),
                         subtask.get("start_timestamp"), subtask.get("end_timestamp"), subtask.get("start_frame"),
                         subtask.get("end_frame"), str(subtask.get("failure_type") or ""),
                         str(subtask.get("summary") or ""), _json(subtask.get("evidence") or {}),
                         _json(subtask.get("metadata") or {})),
                    )
                self.connection.execute(
                    """UPDATE attempts SET outcome=?, success=?, confidence=?, failure_type=?, summary=?, updated_at=?
                    WHERE attempt_id=?""",
                    (evaluation.outcome, None if evaluation.success is None else int(evaluation.success),
                     evaluation.confidence, evaluation.failure_type, evaluation.summary, now, evaluation.attempt_id),
                )
        row = _decode_row(self.connection.execute("SELECT * FROM evaluations WHERE evaluation_id=?", (evaluation_id,)).fetchone())
        assert row is not None
        row["subtasks"] = [
            _decode_row(item) for item in self.connection.execute(
                "SELECT * FROM subtask_results WHERE evaluation_id=? ORDER BY sequence_index, subtask_result_id",
                (evaluation_id,),
            ).fetchall()
        ]
        return row, status

    def events_for_attempt(self, attempt_id: str) -> list[dict[str, Any]]:
        return [
            _decode_row(row) or {} for row in self.connection.execute(
                "SELECT * FROM memory_events WHERE attempt_id=? ORDER BY COALESCE(timestamp,''), event_id", (attempt_id,)
            ).fetchall()
        ]

    def query(self, *, task_id: str, query_type: str, limit: int = 20, policy_version: str = "", outcome: str = "", failure_type: str = "") -> list[dict[str, Any]]:
        limit = max(1, min(500, int(limit)))
        filters = ["a.task_id=?"]
        params: list[Any] = [task_id]
        if outcome:
            filters.append("a.outcome=?")
            params.append(outcome)
        if failure_type:
            filters.append("a.failure_type=?")
            params.append(failure_type)
        if policy_version:
            filters.append("p.version=?")
            params.append(policy_version)
        where = " AND ".join(filters)

        if query_type == "subtask_failure_counts":
            rows = self.connection.execute(
                """SELECT s.subtask_key, s.name, s.failure_type, COUNT(*) AS count,
                   MAX(a.updated_at) AS latest_at
                   FROM subtask_results s JOIN attempts a ON a.attempt_id=s.attempt_id
                   WHERE a.task_id=? AND s.status='failure'
                   GROUP BY s.subtask_key, s.name, s.failure_type
                   ORDER BY count DESC, s.subtask_key, s.failure_type LIMIT ?""",
                (task_id, limit),
            ).fetchall()
            return [dict(row) for row in rows]
        if query_type == "policy_performance":
            rows = self.connection.execute(
                """SELECT COALESCE(p.policy_id,'') AS policy_id, COALESCE(p.version,'') AS policy_version,
                   COUNT(*) AS attempts, SUM(CASE WHEN a.success=1 THEN 1 ELSE 0 END) AS successes,
                   AVG(a.confidence) AS mean_confidence
                   FROM attempts a LEFT JOIN policies p ON p.policy_id=a.policy_id
                   WHERE a.task_id=? GROUP BY p.policy_id, p.version
                   ORDER BY attempts DESC, policy_version, policy_id LIMIT ?""",
                (task_id, limit),
            ).fetchall()
            return [{**dict(row), "success_rate": (row["successes"] / row["attempts"] if row["attempts"] else 0.0)} for row in rows]
        if query_type == "latest_task_state":
            rows = self.connection.execute(
                f"""SELECT a.*, p.version AS policy_version FROM attempts a
                LEFT JOIN policies p ON p.policy_id=a.policy_id WHERE {where}
                ORDER BY a.updated_at DESC, a.attempt_id DESC LIMIT 1""", params
            ).fetchall()
        else:
            extra = ""
            if query_type == "recent_failures":
                extra = " AND a.outcome IN ('failure','partial','unsafe')"
            elif query_type == "successful_attempts":
                extra = " AND a.success=1"
            elif query_type not in {"task_history", "recent_attempts"}:
                raise ValueError(f"unsupported query_type: {query_type}")
            rows = self.connection.execute(
                f"""SELECT a.*, p.version AS policy_version FROM attempts a
                LEFT JOIN policies p ON p.policy_id=a.policy_id WHERE {where}{extra}
                ORDER BY a.updated_at DESC, a.attempt_id DESC LIMIT ?""", [*params, limit]
            ).fetchall()
        return [_decode_row(row) or {} for row in rows]

    def task_summary(self, task_id: str) -> dict[str, Any]:
        task = self.get_task(task_id)
        if task is None:
            raise ValueError(f"task does not exist: {task_id}")
        row = self.connection.execute(
            """SELECT COUNT(*) AS attempts, SUM(CASE WHEN success IS NOT NULL THEN 1 ELSE 0 END) AS evaluated,
            SUM(CASE WHEN success=1 THEN 1 ELSE 0 END) AS successes,
            SUM(CASE WHEN outcome='unsafe' THEN 1 ELSE 0 END) AS unsafe_attempts,
            SUM(CASE WHEN outcome IN ('failure','partial','interrupted','unsafe') THEN 1 ELSE 0 END) AS failure_attempts
            FROM attempts WHERE task_id=?""", (task_id,)
        ).fetchone()
        failures = self.query(task_id=task_id, query_type="subtask_failure_counts", limit=20)
        recent = self.query(task_id=task_id, query_type="recent_attempts", limit=1)
        safety = self.connection.execute(
            """SELECT COUNT(*) FROM memory_events e JOIN attempts a ON a.attempt_id=e.attempt_id
            WHERE a.task_id=? AND (e.severity IN ('error','critical') OR e.event_type IN ('estop','safety_clamp','source_staleness'))""",
            (task_id,),
        ).fetchone()[0]
        serious_safety = self.connection.execute(
            """SELECT COUNT(*) FROM memory_events e JOIN attempts a ON a.attempt_id=e.attempt_id
            WHERE a.task_id=? AND (e.severity='critical' OR e.event_type IN ('estop','source_staleness'))""",
            (task_id,),
        ).fetchone()[0]
        attempts = int(row["attempts"] or 0)
        evaluated = int(row["evaluated"] or 0)
        successes = int(row["successes"] or 0)
        return {
            "task": task, "attempt_count": attempts, "evaluated_attempt_count": evaluated,
            "success_count": successes, "success_rate": successes / evaluated if evaluated else 0.0,
            "unsafe_attempt_count": int(row["unsafe_attempts"] or 0),
            "failure_attempt_count": int(row["failure_attempts"] or 0),
            "safety_event_count": int(safety or 0), "serious_safety_event_count": int(serious_safety or 0),
            "latest_attempt": recent[0] if recent else {},
            "subtask_failures": failures,
        }

    def record_recommendation(self, *, task_id: str, attempt_id: str, recommendation: dict[str, Any], input_value: Any) -> dict[str, Any]:
        fingerprint = content_fingerprint(input_value)
        rule_version = str(recommendation.get("rule_version") or "phase1-v1")
        recommendation_id = stable_id("recommendation", task_id, fingerprint, rule_version)
        with self.connection:
            self.connection.execute(
                """INSERT OR IGNORE INTO recommendations
                (recommendation_id, task_id, attempt_id, rule_version, recommended_action, reason, priority,
                 requires_human_review, supporting_attempt_ids_json, input_fingerprint, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (recommendation_id, task_id, attempt_id or None, rule_version, recommendation["recommended_action"],
                 recommendation["reason"], recommendation["priority"], int(bool(recommendation["requires_human_review"])),
                 _json(recommendation.get("supporting_attempt_ids") or []), fingerprint, utc_now()),
            )
        return _decode_row(self.connection.execute("SELECT * FROM recommendations WHERE recommendation_id=?", (recommendation_id,)).fetchone()) or {}
