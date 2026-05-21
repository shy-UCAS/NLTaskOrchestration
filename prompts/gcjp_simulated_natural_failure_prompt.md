# GCJP v1 Simulated Natural Failure Prompt

You are simulating realistic failed GCJP code produced by an LLM.

Generate Python code only.

Hard requirements:

- Include exactly this import:

```python
from gcjp.mission_graph import TaskGraphBuilder
```

- Create one `TaskGraphBuilder` named `g`.
- Output must be extractable by the evaluator.
- Do not output Markdown fences.
- Do not explain the mistake.
- Do not output empty text.
- The code should look like a genuine attempt to solve the task.
- Inject exactly one realistic mistake described by `BUG_SPEC`.
- Keep all unrelated parts as correct and faithful as possible.
- End with `built = g.build()` unless `BUG_SPEC.bug_type` is `missing_built`.
- Except for the single requested bug, all GCJP API calls must use the correct
  signatures below.

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

Correct GCJP API signatures:

- Normal builder construction must use both constructor arguments:

```python
g = TaskGraphBuilder(segment_id="<segment_id>", assigned_actors=[...])
```

- Only use `TaskGraphBuilder()` when `BUG_SPEC.bug_type` is
  `builder_missing_args`.
- Declare segment metadata without `segment_id`, `actor`, or
  `assigned_actors`:

```python
g.declare_segment_meta(assumed_conditions=[...])
```

- Add tasks with the real field names:

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

- `required_capability` is mandatory unless `BUG_SPEC.bug_type` is
  `missing_required_capability`. If no capability is needed, pass
  `required_capability=[]`.
- Add dependencies with `source` and `target` task IDs:

```python
g.add_dependency("<source>", "<target>", relation="<relation>")
```

- Add standalone time order constraints with:

```python
g.add_time_order_constraint("<before_task>", "<after_task>")
```

- Add standalone time windows with:

```python
g.add_time_window_constraint("<task_id>", earliest=<earliest>, latest=<latest>, deadline=<deadline>)
```

Only include `earliest`, `latest`, or `deadline` values that are needed.

- Add resource constraints with:

```python
g.add_resource_constraint("<actor>", "<resource_type>", max_value=<max_value>)
```

Use `resource_type="ammo"` or `resource_type="energy_kwh"`.

- Add capability constraints with:

```python
g.add_capability_constraint("<task_id>", required=[...], actor_capabilities=[...])
```

- Add physical feasibility constraints with:

```python
g.add_physical_feasibility_constraint(
    "<task_id>",
    from_position="<from_position>",
    to_position="<to_position>",
    distance_km=<distance_km>,
    actor_speed_kmh=<actor_speed_kmh>,
)
```

Forbidden old or hallucinated parameter names unless the target bug explicitly
requires them:

- Do not use `duration`; use `duration_lb`.
- Do not use `task_type`; use `action`.
- Do not use `resources`; use `energy_cost` and `ammo_cost`.
- Do not use `from_task` or `to_task`; use `source` and `target`.
- Do not use `predecessor` or `successor`; use `before` and `after`.
- Do not use `resource`; use `resource_type`.
- Do not use `speed_kmh`; use `actor_speed_kmh`.
- Do not pass `segment_id`, `actor`, or `assigned_actors` to
  `declare_segment_meta`.

Minimal correct skeleton before injecting a bug:

```python
from gcjp.mission_graph import TaskGraphBuilder
g = TaskGraphBuilder(segment_id="seg_example", assigned_actors=["fleet_1"])
g.declare_segment_meta(assumed_conditions=["mission_start"])
g.add_task("t1", actor="fleet_1", action="reconnaissance", target="area_A", duration_lb=1.0, required_capability=["recon_capable"], energy_cost=1.0, ammo_cost=0)
g.add_resource_constraint("fleet_1", "ammo", max_value=4)
built = g.build()
```

Realistic bug guidance:

- `missing_built`: omit only `built = g.build()`. Keep constructor, tasks,
  dependencies, and constraints correct.
- `builder_missing_args`: call `TaskGraphBuilder()` without constructor arguments.
- `api_misuse_add_constraint`: use unsupported `g.add_constraint(...)`.
- `missing_required_capability`: omit the mandatory `required_capability` argument from one `add_task`.
- `missing_resource_constraint`: omit the target resource constraint.
- `wrong_resource_bound`: include the resource constraint but use the wrong `max_value`.
- `missing_time_window`: omit the target time-window constraint.
- `wrong_relation_type`: use a plausible but wrong relation type for one dependency.
- `missing_group_sync`: omit the target group sync constraint.
- `wrong_physical_speed`: include physical feasibility but use the wrong `actor_speed_kmh`.
  Preserve any source deadline/time-window constraints exactly, especially
  `deadline` values. Do not create this bug by deleting the deadline, deleting
  the physical feasibility constraint, changing `distance_km`, changing task
  duration, or changing `expected_result`.
- `missing_capability_constraint`: omit the target standalone capability constraint.

Do not combine bug types unless the bug spec explicitly asks for multiple bugs.

If `BUG_SPEC.bug_type` is not `missing_built`, the final line must be exactly:

```python
built = g.build()
```

Normal source task:

{{SOURCE_CASE_JSON}}

Standard instruction if present:

{{STANDARD_INSTRUCTION}}

BUG_SPEC:

{{BUG_SPEC_JSON}}

Full simulated failure case metadata:

{{CASE_JSON}}
