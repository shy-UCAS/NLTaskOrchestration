# GCJP v1 Code Generation Prompt Few-shot

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

API contract:

- Always construct `g` with `TaskGraphBuilder(segment_id="<segment_id>", assigned_actors=[...])`.
- Never call `TaskGraphBuilder()` without arguments.
- Call `g.declare_segment_meta(assumed_conditions=[...])`; do not pass `segment_id` or `assigned_actors` there.
- `required_capability` is mandatory in `add_task`. If no capability is required, pass `required_capability=[]`.
- Use the input `action` string exactly. For flight tasks use `action="fly_to"`, never `action="fly"`.
- `add_task` does not accept `condition`. If a task or relation has a condition, put it only on `g.add_dependency(..., relation="condition_trigger", condition="<condition>")`.
- For task relations, call `g.add_dependency("<source>", "<target>", relation="<relation>")`.
- For physical feasibility, call `g.add_physical_feasibility_constraint("<task_id>", from_position="<from>", to_position="<to>", distance_km=<distance>, actor_speed_kmh=<speed>, time_unit_minutes=<minutes>)`. Never use `speed_kmh`.
- Do not duplicate relation-derived time/sync constraints unless the input contains a standalone explicit constraint.
- Valid `relation` values: `sequence`, `parallel`, `sync`, `barrier`, `condition_trigger`, `handoff`, `fork`, `join` (`conditional` is an accepted alias of `condition_trigger`; prefer `condition_trigger`).
- For a capability constraint, call `g.add_capability_constraint("<task_id>", required=[...], actor_capabilities=[...])`.
- For standalone constraints not already implied by a relation, call `g.add_time_order_constraint("<before_task_id>", "<after_task_id>")`, `g.add_sync_constraint("<task_i>", "<task_j>", tolerance=<tolerance>)`, or `g.add_group_sync_constraint(["<task_id>", "<task_id>"], tolerance=<tolerance>, mode="start")`. These use `tolerance=`; never pass `sync_tolerance=` (only `add_dependency` accepts `sync_tolerance`). `mode` is one of `start`, `end`, `both`.
- Only when the input explicitly requires an exit resource state or interface fulfillment, call `g.declare_resource_state("<actor>", remaining_ammo=<remaining_ammo>, remaining_energy=<remaining_energy>, position="<position>")` or `g.declare_interface_fulfillment("<interface_id>", exit_node="<task_id>", resource_state={...}, guaranteed_conditions=[...])`.
- End with exactly one exported graph variable: `built = g.build()`.

Few-shot example 1:

Input:

```json
{
  "segment_id": "seg_fs_sequence",
  "assigned_actors": ["fleet_x"],
  "assumed_conditions": ["mission_start"],
  "tasks": [
    {"task_id": "fs_t1_recon", "actor": "fleet_x", "action": "reconnaissance", "target": "zone_x", "duration_lb": 1.0, "required_capability": ["recon_capable"], "energy_cost": 1.0, "ammo_cost": 0},
    {"task_id": "fs_t2_strike", "actor": "fleet_x", "action": "strike", "target": "target_x", "duration_lb": 1.0, "required_capability": ["strike_capable"], "energy_cost": 2.0, "ammo_cost": 1, "time_window": {"deadline": 8.0}}
  ],
  "relations": [{"source": "fs_t1_recon", "target": "fs_t2_strike", "relation": "sequence"}],
  "constraints": [{"constraint_type": "resource", "actor": "fleet_x", "resource_type": "ammo", "max_value": 2}]
}
```

Output:

```python
from gcjp.mission_graph import TaskGraphBuilder
g = TaskGraphBuilder(segment_id="seg_fs_sequence", assigned_actors=["fleet_x"])
g.declare_segment_meta(assumed_conditions=["mission_start"])
g.add_task("fs_t1_recon", actor="fleet_x", action="reconnaissance", target="zone_x", duration_lb=1.0, required_capability=["recon_capable"], energy_cost=1.0, ammo_cost=0)
g.add_task("fs_t2_strike", actor="fleet_x", action="strike", target="target_x", duration_lb=1.0, required_capability=["strike_capable"], energy_cost=2.0, ammo_cost=1)
g.add_dependency("fs_t1_recon", "fs_t2_strike", relation="sequence")
g.add_time_window_constraint("fs_t2_strike", deadline=8.0)
g.add_resource_constraint("fleet_x", "ammo", max_value=2)
built = g.build()
```

Few-shot example 2:

Input:

```json
{
  "segment_id": "seg_fs_sync",
  "assigned_actors": ["fleet_x", "fleet_y"],
  "assumed_conditions": [],
  "tasks": [
    {"task_id": "fs_x_ready", "actor": "fleet_x", "action": "rendezvous", "target": "point_x", "duration_lb": 0.5, "required_capability": [], "energy_cost": 1.0, "ammo_cost": 0},
    {"task_id": "fs_y_ready", "actor": "fleet_y", "action": "rendezvous", "target": "point_x", "duration_lb": 0.5, "required_capability": [], "energy_cost": 1.0, "ammo_cost": 0}
  ],
  "relations": [{"source": "fs_x_ready", "target": "fs_y_ready", "relation": "sync", "sync_tolerance": 0.5}],
  "constraints": []
}
```

Output:

```python
from gcjp.mission_graph import TaskGraphBuilder
g = TaskGraphBuilder(segment_id="seg_fs_sync", assigned_actors=["fleet_x", "fleet_y"])
g.declare_segment_meta(assumed_conditions=[])
g.add_task("fs_x_ready", actor="fleet_x", action="rendezvous", target="point_x", duration_lb=0.5, required_capability=[], energy_cost=1.0, ammo_cost=0)
g.add_task("fs_y_ready", actor="fleet_y", action="rendezvous", target="point_x", duration_lb=0.5, required_capability=[], energy_cost=1.0, ammo_cost=0)
g.add_dependency("fs_x_ready", "fs_y_ready", relation="sync", sync_tolerance=0.5)
built = g.build()
```

Few-shot example 3:

Input:

```json
{
  "segment_id": "seg_fs_condition_physical",
  "assigned_actors": ["fleet_z"],
  "assumed_conditions": ["mission_start"],
  "tasks": [
    {"task_id": "fs_fly", "actor": "fleet_z", "action": "fly_to", "target": "point_z", "duration_lb": 9.0, "required_capability": [], "energy_cost": 2.0, "ammo_cost": 0},
    {"task_id": "fs_recon", "actor": "fleet_z", "action": "reconnaissance", "target": "point_z", "duration_lb": 2.0, "required_capability": ["recon_capable"], "energy_cost": 3.0, "ammo_cost": 0, "condition": "arrived_point_z"}
  ],
  "relations": [{"source": "fs_fly", "target": "fs_recon", "relation": "condition_trigger", "condition": "arrived_point_z"}],
  "constraints": [{"constraint_type": "physical_feasibility", "task_id": "fs_fly", "from_position": "base_z", "to_position": "point_z", "distance_km": 10.0, "actor_speed_kmh": 70.0, "time_unit_minutes": 1.0}]
}
```

Output:

```python
from gcjp.mission_graph import TaskGraphBuilder
g = TaskGraphBuilder(segment_id="seg_fs_condition_physical", assigned_actors=["fleet_z"])
g.declare_segment_meta(assumed_conditions=["mission_start"])
g.add_task("fs_fly", actor="fleet_z", action="fly_to", target="point_z", duration_lb=9.0, required_capability=[], energy_cost=2.0, ammo_cost=0)
g.add_task("fs_recon", actor="fleet_z", action="reconnaissance", target="point_z", duration_lb=2.0, required_capability=["recon_capable"], energy_cost=3.0, ammo_cost=0)
g.add_dependency("fs_fly", "fs_recon", relation="condition_trigger", condition="arrived_point_z")
g.add_physical_feasibility_constraint("fs_fly", from_position="base_z", to_position="point_z", distance_km=10.0, actor_speed_kmh=70.0, time_unit_minutes=1.0)
built = g.build()
```

Now generate code for this input structured case:

{{CASE_JSON}}
