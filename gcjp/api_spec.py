"""
gcjp/api_spec.py

Single source of truth for the frozen GCJP restricted API v1.
LLM-generated GCJP code should be checked against this file.
"""

GCJP_API_VERSION = "v1"

ALLOWED_IMPORTS = {
    "gcjp.mission_graph",
}

ALLOWED_BUILDER_METHODS = {
    "declare_segment_meta",
    "add_task",
    "add_dependency",
    "add_time_order_constraint",
    "add_time_window_constraint",
    "add_sync_constraint",
    "add_group_sync_constraint",
    "add_resource_constraint",
    "add_capability_constraint",
    "add_physical_feasibility_constraint",
    "declare_resource_state",
    "declare_interface_fulfillment",
    "build",
}

VALID_RELATION_TYPES = {
    "sequence",
    "parallel",
    "sync",
    "barrier",
    "condition_trigger",
    "handoff",
    "fork",
    "join",
}

RELATION_ALIASES = {
    "conditional": "condition_trigger",
}

VALID_CONSTRAINT_TYPES = {
    "time_order",
    "duration",
    "time_window",
    "sync",
    "group_sync",
    "resource",
    "capability",
    "physical_feasibility",
}

VALID_RESOURCE_TYPES = {
    "ammo",
    "energy_kwh",
}

VALID_TASK_METADATA_KEYS = {
    "condition",
    "expected_output",
    "source",
    "priority",
}
