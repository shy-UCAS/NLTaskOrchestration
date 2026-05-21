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

Realistic bug guidance:

- `missing_built`: omit `built = g.build()`.
- `builder_missing_args`: call `TaskGraphBuilder()` without constructor arguments.
- `api_misuse_add_constraint`: use unsupported `g.add_constraint(...)`.
- `missing_required_capability`: omit the mandatory `required_capability` argument from one `add_task`.
- `missing_resource_constraint`: omit the target resource constraint.
- `wrong_resource_bound`: include the resource constraint but use the wrong `max_value`.
- `missing_time_window`: omit the target time-window constraint.
- `wrong_relation_type`: use a plausible but wrong relation type for one dependency.
- `missing_group_sync`: omit the target group sync constraint.
- `wrong_physical_speed`: include physical feasibility but use the wrong `actor_speed_kmh`.
- `missing_capability_constraint`: omit the target standalone capability constraint.

Do not combine bug types unless the bug spec explicitly asks for multiple bugs.

Normal source task:

{{SOURCE_CASE_JSON}}

Standard instruction if present:

{{STANDARD_INSTRUCTION}}

BUG_SPEC:

{{BUG_SPEC_JSON}}

Full simulated failure case metadata:

{{CASE_JSON}}
