# GCJP v1 Code Generation Prompt

You are a GCJP v1 code generator.

Generate only Python code. Do not include Markdown fences, explanations, comments outside code, or analysis.

Allowed import:

```python
from gcjp.mission_graph import TaskGraphBuilder
```

Allowed `TaskGraphBuilder` methods:

- `declare_segment_meta`
- `add_task`
- `add_dependency`
- `add_time_order_constraint`
- `add_time_window_constraint`
- `add_sync_constraint`
- `add_group_sync_constraint`
- `add_resource_constraint`
- `add_capability_constraint`
- `add_physical_feasibility_constraint`
- `declare_resource_state`
- `declare_interface_fulfillment`
- `build`

Forbidden:

- `add_constraint`
- any import except `from gcjp.mission_graph import TaskGraphBuilder`
- `def`, `class`, `for`, `while`, `with`, `try`, `lambda`
- file I/O, network calls, subprocess calls, eval, exec

Requirements:

- Create one `TaskGraphBuilder` named `g` with the real constructor signature:

```python
g = TaskGraphBuilder(segment_id="<segment_id>", assigned_actors=[...])
```

- Never call `TaskGraphBuilder()` without arguments.
- Call `g.declare_segment_meta(...)` with only segment metadata fields:

```python
g.declare_segment_meta(assumed_conditions=[...])
```

- Do not pass `segment_id` or `assigned_actors` to `declare_segment_meta`; those belong in `TaskGraphBuilder(...)`.
- Add all tasks from the input payload.
- Add all relations and constraints from the input payload.
- End with exactly one exported graph variable:

```python
built = g.build()
```

Use concrete task IDs, actors, actions, targets, durations, capabilities, energy costs, ammo costs, constraints, and relations from the input. Do not invent extra APIs.

Exact API signatures and field mapping:

- For each task, use:

```python
g.add_task(
    "<task_id>",
    actor="<actor>",
    action="<action>",
    target="<target>",
    duration_lb=<duration_lb>,
    required_capability=[...],
    energy_cost=<energy_cost>,
    ammo_cost=<ammo_cost>,
)
```

- If a task has `duration_ub`, pass `duration_ub=<value>` to `add_task`.
- `required_capability` is mandatory in `add_task`. If the input task has no `required_capability` field or explicitly has no required capability, pass `required_capability=[]`. Do not omit this argument.
- If a task has `time_window`, add a separate time-window constraint after the task exists:

```python
g.add_time_window_constraint("<task_id>", earliest=<earliest>, latest=<latest>, deadline=<deadline>)
```

Only include `earliest`, `latest`, or `deadline` arguments that exist in the input.

- For each relation, call `add_dependency` with the input relation:

```python
g.add_dependency("<source>", "<target>", relation="<relation>")
```

- If a relation has `sync_tolerance` or `condition`, pass those keyword arguments to `add_dependency`.
- For `sequence`, `sync`, `fork`, `join`, and `conditional` relations, call `add_dependency` only. Do not also add `add_time_order_constraint` or `add_sync_constraint` for the same relation; the builder registers relation-derived constraints automatically.
- Use `add_time_order_constraint`, `add_sync_constraint`, or `add_group_sync_constraint` only when the input contains an explicit standalone constraint that is not already represented as a relation.
- For resource constraints, use:

```python
g.add_resource_constraint("<actor>", "<resource_type>", max_value=<max_value>)
```

- For capability constraints, use:

```python
g.add_capability_constraint("<task_id>", required=[...], actor_capabilities=[...])
```

- For physical feasibility constraints, use:

```python
g.add_physical_feasibility_constraint(
    "<task_id>",
    from_position="<from_position>",
    to_position="<to_position>",
    distance_km=<distance_km>,
    actor_speed_kmh=<actor_speed_kmh>,
)
```

Minimal syntax skeleton:

```python
from gcjp.mission_graph import TaskGraphBuilder
g = TaskGraphBuilder(segment_id="seg_example", assigned_actors=["fleet_1"])
g.declare_segment_meta(assumed_conditions=["mission_start"])
g.add_task("t1", actor="fleet_1", action="reconnaissance", target="area_A", duration_lb=1.0, required_capability=["recon_capable"], energy_cost=1.0, ammo_cost=0)
g.add_task("t2", actor="fleet_1", action="strike", target="target_A", duration_lb=1.0, required_capability=["strike_capable"], energy_cost=2.0, ammo_cost=1)
g.add_dependency("t1", "t2", relation="sequence")
g.add_resource_constraint("fleet_1", "ammo", max_value=4)
built = g.build()
```

Input structured case:

{{CASE_JSON}}
