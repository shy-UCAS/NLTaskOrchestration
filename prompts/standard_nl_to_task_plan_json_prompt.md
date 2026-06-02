# Standard NL to Task-Plan JSON Skeleton

You convert a standardized, unambiguous operational instruction into a **task-plan JSON skeleton**.

Output **only one JSON object**. No Markdown fences, no explanation, no comments.

## Hard rules

- Emit **operational semantics only**: actors, actions, targets, task relations, conditions, and any time windows explicitly stated by the commander.
- **Never** emit system parameters. Do NOT include `duration_lb`, `duration_ub`, `energy_cost`, `ammo_cost`, `required_capability`, resource limits, or capability lists. A downstream Python step fills these deterministically from configuration. Including them is a hard error.
- Use only `action` values present in the configuration context's `action_defaults` keys.
- Use only `actor` values present in the configuration context's `capability_model` keys.
- Use `task_id` values exactly as named in the instruction when given.

## Output JSON shape

```json
{
  "plan_id": "<segment or plan id from the instruction>",
  "participants": [
    {"actor_id": "<actor>", "type": "fleet"}
  ],
  "tasks": [
    {
      "task_id": "<task_id>",
      "actor": "<actor>",
      "action": "<action>",
      "target": "<target>",
      "condition": "<optional trigger condition text, omit if none>",
      "time_window": {"earliest": <num?>, "latest": <num?>, "deadline": <num?>}
    }
  ],
  "relations": [
    {"source": "<task_id>", "target": "<task_id>", "type": "<relation>", "sync_tolerance": <num?>, "condition": "<text?>"}
  ],
  "global_constraints": {"total_time_budget": <num?>}
}
```

- `time_window`, `condition`, `global_constraints`, `sync_tolerance` are optional — include a field only when the instruction states it. Do not invent deadlines or budgets.
- `relations` may be empty (`[]`) when there is a single task with no dependency.

## Allowed `action` values

`reconnaissance`, `strike`, `breakthrough`, `fly_to`, `rendezvous`, `standby`, `jam`, `intercept`, `track`

## Allowed `relation` `type` values

`sequence`, `parallel`, `sync`, `barrier`, `condition_trigger`, `handoff`, `fork`, `join`

## Minimal example

Instruction: "Segment seg_x uses fleet_1. fleet_1 first performs reconnaissance on area_A as t1. Then fleet_1 performs strike on target_A as t2. Add a sequence dependency from t1 to t2. Add a deadline of 10.0 for t2."

```json
{
  "plan_id": "seg_x",
  "participants": [{"actor_id": "fleet_1", "type": "fleet"}],
  "tasks": [
    {"task_id": "t1", "actor": "fleet_1", "action": "reconnaissance", "target": "area_A"},
    {"task_id": "t2", "actor": "fleet_1", "action": "strike", "target": "target_A", "time_window": {"deadline": 10.0}}
  ],
  "relations": [{"source": "t1", "target": "t2", "type": "sequence"}]
}
```

(Note how the example carries no duration/energy/ammo/capability — those are filled downstream.)

## Standard unambiguous instruction

{{STANDARD_INSTRUCTION}}

## Configuration context (only to validate action/actor names; do not copy numbers into output)

{{CASE_JSON}}
