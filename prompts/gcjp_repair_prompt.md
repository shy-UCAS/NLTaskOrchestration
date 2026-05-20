# GCJP v1 Repair Prompt

You are repairing GCJP v1 Python code.

Return only the repaired Python code. Do not include Markdown fences, explanations, comments outside code, or analysis.

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

Repair rules:

- Preserve the task semantics from the case payload.
- Do not delete tasks, dependencies, or constraints just to pass validation.
- Fix the smallest code region that explains the failure.
- Prioritize Layer 1 diagnostics: `gcjp_lineno`, `source_context`, `traceback_text`, `api_error.code`, and `structured_violations`.
- Always construct `g` with `TaskGraphBuilder(segment_id="<segment_id>", assigned_actors=[...])`.
- Never call `TaskGraphBuilder()` without arguments.
- `add_task` requires `required_capability`; use `required_capability=[]` when no capability is required.
- `add_task` does not accept `condition`; use `add_dependency(..., relation="condition_trigger", condition="<condition>")`.
- For physical feasibility use `actor_speed_kmh`, not `speed_kmh`.
- For flight tasks use `action="fly_to"`, not `action="fly"`.
- End with exactly one exported graph variable: `built = g.build()`.

Original prompt context:

{{PROMPT_CONTEXT}}

Case payload:

{{CASE_JSON}}

Broken GCJP code:

```python
{{BROKEN_CODE}}
```

Verification report:

```json
{{VERIFICATION_REPORT_JSON}}
```
