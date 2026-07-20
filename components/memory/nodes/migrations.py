"""SQLite migrations for the Phase 1 memory store."""
from __future__ import annotations

MIGRATIONS: tuple[tuple[int, str], ...] = (
    (1, r"""
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE tasks (
    task_id TEXT PRIMARY KEY,
    stable_key TEXT NOT NULL UNIQUE,
    task_name TEXT NOT NULL,
    task_description TEXT NOT NULL DEFAULT '',
    robot_id TEXT NOT NULL DEFAULT '',
    environment TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','paused','complete','archived')),
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE artifacts (
    artifact_id TEXT PRIMARY KEY,
    source_type TEXT NOT NULL,
    normalized_path TEXT NOT NULL,
    workspace_relative_path TEXT NOT NULL DEFAULT '',
    logical_id TEXT NOT NULL DEFAULT '',
    fingerprint TEXT NOT NULL,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    modified_at_ns INTEGER NOT NULL DEFAULT 0,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    ingested_at TEXT NOT NULL,
    UNIQUE(source_type, normalized_path, logical_id)
);

CREATE TABLE policies (
    policy_id TEXT PRIMARY KEY,
    artifact_id TEXT REFERENCES artifacts(artifact_id),
    artifact_path TEXT NOT NULL,
    artifact_digest TEXT NOT NULL,
    policy_type TEXT NOT NULL DEFAULT '',
    version TEXT NOT NULL DEFAULT '',
    training_dataset TEXT NOT NULL DEFAULT '',
    training_step INTEGER,
    created_at TEXT,
    metrics_json TEXT NOT NULL DEFAULT '{}',
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE attempts (
    attempt_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE RESTRICT,
    episode_artifact_id TEXT REFERENCES artifacts(artifact_id),
    policy_log_artifact_id TEXT REFERENCES artifacts(artifact_id),
    dataset_id TEXT NOT NULL DEFAULT '',
    episode_index INTEGER,
    policy_run_id TEXT NOT NULL DEFAULT '',
    policy_id TEXT REFERENCES policies(policy_id),
    started_at TEXT,
    ended_at TEXT,
    outcome TEXT NOT NULL DEFAULT 'unknown' CHECK (outcome IN ('unknown','success','failure','partial','interrupted','unsafe')),
    success INTEGER CHECK (success IS NULL OR success IN (0,1)),
    confidence REAL CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
    failure_type TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX attempts_episode_identity ON attempts(task_id, dataset_id, episode_index)
    WHERE dataset_id <> '' AND episode_index IS NOT NULL;
CREATE UNIQUE INDEX attempts_run_identity ON attempts(task_id, policy_run_id)
    WHERE policy_run_id <> '';

CREATE TABLE evaluations (
    evaluation_id TEXT PRIMARY KEY,
    attempt_id TEXT NOT NULL REFERENCES attempts(attempt_id) ON DELETE CASCADE,
    evaluator_type TEXT NOT NULL,
    evaluator_name TEXT NOT NULL DEFAULT '',
    evaluator_version TEXT NOT NULL DEFAULT '',
    outcome TEXT NOT NULL CHECK (outcome IN ('unknown','success','failure','partial','interrupted','unsafe')),
    success INTEGER CHECK (success IS NULL OR success IN (0,1)),
    confidence REAL CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
    failure_type TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    evidence_json TEXT NOT NULL DEFAULT '{}',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    content_fingerprint TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(attempt_id, content_fingerprint)
);

CREATE TABLE subtask_results (
    subtask_result_id TEXT PRIMARY KEY,
    evaluation_id TEXT NOT NULL REFERENCES evaluations(evaluation_id) ON DELETE CASCADE,
    attempt_id TEXT NOT NULL REFERENCES attempts(attempt_id) ON DELETE CASCADE,
    subtask_key TEXT NOT NULL,
    name TEXT NOT NULL,
    sequence_index INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('unknown','not_started','in_progress','success','failure','skipped')),
    confidence REAL CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
    start_timestamp TEXT,
    end_timestamp TEXT,
    start_frame INTEGER,
    end_frame INTEGER,
    failure_type TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    evidence_json TEXT NOT NULL DEFAULT '{}',
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE memory_events (
    event_id TEXT PRIMARY KEY,
    attempt_id TEXT NOT NULL REFERENCES attempts(attempt_id) ON DELETE CASCADE,
    artifact_id TEXT REFERENCES artifacts(artifact_id),
    source_event_key TEXT NOT NULL,
    event_type TEXT NOT NULL,
    timestamp TEXT,
    frame_index INTEGER,
    source TEXT NOT NULL DEFAULT '',
    severity TEXT NOT NULL DEFAULT 'info' CHECK (severity IN ('info','warning','error','critical')),
    summary TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(attempt_id, source_event_key)
);

CREATE TABLE recommendations (
    recommendation_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
    attempt_id TEXT REFERENCES attempts(attempt_id) ON DELETE SET NULL,
    rule_version TEXT NOT NULL,
    recommended_action TEXT NOT NULL,
    reason TEXT NOT NULL,
    priority TEXT NOT NULL,
    requires_human_review INTEGER NOT NULL CHECK (requires_human_review IN (0,1)),
    supporting_attempt_ids_json TEXT NOT NULL DEFAULT '[]',
    input_fingerprint TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(task_id, input_fingerprint, rule_version)
);

CREATE INDEX attempts_task_updated ON attempts(task_id, updated_at DESC, attempt_id DESC);
CREATE INDEX events_attempt_timestamp ON memory_events(attempt_id, timestamp, event_id);
CREATE INDEX subtasks_attempt_status ON subtask_results(attempt_id, status, subtask_key);
"""),
)
