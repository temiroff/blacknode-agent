# First robot-memory experiment

This experiment uses one recorded demonstration and one deployed policy to
separate memorization, generalization, evaluation, and learning.

## Expected result

With one demonstration, an imitation policy may reproduce the demonstrated
motion when the cube, camera, robot pose, lighting, and target remain close to
the recording. Moving the cube left tests visual and action generalization. The
policy may succeed, partially recover, or follow the original trajectory and
miss the cube.

Blacknode Memory records both outcomes. Phase 1 keeps weights fixed during the
moved-cube attempt. After evaluation it can remember `scene_shift` at the
`locate green cube` subtask and recommend a retry. A repeated failure produces
`collect_correction`, which tells the operator to record a corrected
demonstration from the shifted position.

## Preparation

1. Keep the robot supported and begin with deployment disarmed.
2. Preserve the original episode. Newly recorded Blacknode episodes now receive
   a stable `episode_id`, `run_id`, `started_at`, and `completed_at`. Existing
   episodes remain ingestible through `(dataset_id, episode_index)`.
3. Export or select the policy artifact trained from the original recording.
4. Open **Robot Memory and Improvement Review**.
5. Enter the dataset directory, episode ID or index, policy-run JSONL path, and
   policy artifact path.

Index the original training episode with `attempt_role=demonstration`. Record a
new episode for each deployed trial and use `attempt_role=deployment`; this
keeps demonstration evidence distinct from policy-execution experience.

## Baseline attempt: demonstrated scene

1. Set `RobotTaskCreate.action=create` once. Return it to `get` or `check`.
2. Deploy through the existing policy-runtime workflow in disarmed preview.
3. Verify camera names, joint order, observation freshness, calibration, and
   safety limits.
4. Explicitly arm and start the attempt using the deployment runtime.
5. Disarm when the attempt completes.
6. Set `EpisodeMemoryIngest.action=ingest` once, then return it to `status`.
7. Record a human evaluation through `TaskEvaluationRecord.action=record`.
   Describe which subtasks succeeded and attach camera/frame evidence when
   available. Return the node to `status`.

## Changed-scene attempt: cube moved left

1. Move only the green cube left. Keep the remaining scene controlled for the
   first comparison.
2. Start a new policy run ID and deploy the same immutable policy artifact.
3. Observe whether it locates, approaches, grasps, lifts, and places the cube.
4. Ingest the new policy log and its associated recorded episode as a second
   attempt under the same task.
5. Record the outcome. If it misses the shifted cube, use:

```json
{
  "outcome": "failure",
  "success": false,
  "confidence": 0.95,
  "failure_type": "scene_shift",
  "summary": "The cube moved left and was not located.",
  "subtasks": [
    {
      "name": "locate green cube",
      "sequence_index": 0,
      "status": "failure",
      "confidence": 0.95,
      "failure_type": "scene_shift"
    }
  ],
  "evidence": {"camera": "front"},
  "evaluator": {"type": "human", "name": "operator"}
}
```

6. Query `recent_failures` and `subtask_failure_counts`.
7. Review the recommendation. The first ordinary failure should recommend
   `retry`; the same evaluated subtask failure twice should recommend
   `collect_correction`.

## Command interaction after deployment

Treat “pick up the green cube” as a request to the task layer. Motor
authorization remains a separate explicit action:

```text
Operator text or speech
  → task resolver
  → task-memory lookup
  → compatible policy/skill selection
  → readiness and scene checks
  → disarmed preview
  → explicit operator arm/start
  → safety-gated execution
  → evaluation and memory
```

The current single-task ACT policy can execute its learned task after explicit
start and arming. A future command node can create/select the task from language,
while retaining arming, freshness, joint-limit, takeover, and e-stop controls.
Language-conditioned policy selection or a VLA model is a separate
capability from task naming.

## What constitutes learning

For this first phase, learning means accumulating structured evidence and
choosing the next improvement action. Policy learning begins when a corrected
demonstration is added to a candidate dataset, a new candidate is trained, and
that candidate passes replay or simulation validation before explicit
promotion.
