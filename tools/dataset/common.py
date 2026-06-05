from __future__ import annotations

import copy
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any


SYSTEM_PARAM_FIELDS = {
    "duration_lb",
    "duration_ub",
    "energy_cost",
    "ammo_cost",
    "required_capability",
    "max_ammo",
    "max_energy_kwh",
    "actor_capabilities",
}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                record = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_num}: invalid JSON: {exc}") from exc
            if not isinstance(record, dict):
                raise ValueError(f"{path}:{line_num}: JSONL row must be an object")
            records.append(record)
    return records


def write_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            count += 1
    return count


def expected_graph_from_plan(
    plan: dict[str, Any] | None,
    expected_patterns: dict[str, Any] | None = None,
) -> dict[str, Any]:
    expected_patterns = expected_patterns or {}
    if not plan:
        return {
            "node_count": int(expected_patterns.get("node_count", 0) or 0),
            "nodes": [],
            "edge_count": 0,
            "edges": [],
            "constraint_types": list(expected_patterns.get("constraint_types", []) or []),
        }

    tasks = plan.get("tasks", []) or []
    relations = plan.get("relations", []) or []
    return {
        "node_count": len(tasks),
        "nodes": [
            {
                "task_id": str(task.get("task_id", "")),
                "actor": str(task.get("actor", "")),
                "action": str(task.get("action", "")),
                "target": str(task.get("target", "")),
            }
            for task in tasks
        ],
        "edge_count": len(relations),
        "edges": [
            {
                "source": str(rel.get("source", "")),
                "target": str(rel.get("target", "")),
                "relation": str(rel.get("type") or rel.get("relation", "")),
            }
            for rel in relations
        ],
        "constraint_types": list(expected_patterns.get("constraint_types", []) or []),
    }


def expected_patterns_from_graph(expected_graph: dict[str, Any]) -> dict[str, Any]:
    relations = []
    for edge in expected_graph.get("edges", []) or []:
        relation = edge.get("relation")
        if relation and relation not in relations:
            relations.append(relation)
    return {
        "node_count": expected_graph.get("node_count", 0),
        "edge_relations": relations,
        "constraint_types": list(expected_graph.get("constraint_types", []) or []),
    }


def convert_payload_to_canonical_plan(payload: dict[str, Any]) -> dict[str, Any]:
    """Convert legacy structured input_payload into system-param-free task plan."""
    assigned_actors = payload.get("assigned_actors") or []
    plan = {
        "plan_id": str(payload.get("segment_id") or payload.get("plan_id") or "unknown_plan"),
        "participants": [
            {"actor_id": str(actor), "type": "fleet"}
            for actor in assigned_actors
        ],
        "tasks": [],
        "relations": [],
        "global_constraints": {},
        "explicit_constraints": [],
    }

    for task in payload.get("tasks", []) or []:
        clean_task = {
            "task_id": str(task.get("task_id", "")),
            "actor": str(task.get("actor", "")),
            "action": str(task.get("action", "")),
            "target": str(task.get("target", "")),
            "condition": task.get("condition"),
            "time_window": copy.deepcopy(task.get("time_window")) if task.get("time_window") else None,
            "metadata": {},
        }
        if "is_coalition" in task:
            clean_task["is_coalition"] = bool(task.get("is_coalition"))
        if "coalition_members" in task:
            clean_task["coalition_members"] = list(task.get("coalition_members") or [])
        plan["tasks"].append(clean_task)

    for rel in payload.get("relations", []) or []:
        plan["relations"].append(
            {
                "source": str(rel.get("source", "")),
                "target": str(rel.get("target", "")),
                "type": str(rel.get("type") or rel.get("relation") or "sequence"),
                "sync_tolerance": rel.get("sync_tolerance"),
                "condition": rel.get("condition"),
            }
        )

    constraints = payload.get("constraints", []) or []
    for constraint in constraints:
        ctype = str(constraint.get("type") or constraint.get("constraint_type") or "")
        if ctype == "time_window" and constraint.get("task_id") and constraint.get("deadline") is not None:
            _merge_deadline(plan, str(constraint["task_id"]), constraint["deadline"])
            continue
        converted = copy.deepcopy(constraint)
        converted["type"] = ctype
        converted.pop("constraint_type", None)
        plan["explicit_constraints"].append(converted)

    if not plan["participants"]:
        actors = sorted({task["actor"] for task in plan["tasks"] if task.get("actor")})
        plan["participants"] = [{"actor_id": actor, "type": "fleet"} for actor in actors]

    return plan


def _merge_deadline(plan: dict[str, Any], task_id: str, deadline: Any) -> None:
    for task in plan.get("tasks", []):
        if task.get("task_id") != task_id:
            continue
        window = task.get("time_window") or {}
        window["deadline"] = deadline
        task["time_window"] = window
        return


def build_task_plan_for_loader(plan: dict[str, Any]) -> dict[str, Any]:
    """Convert canonical_task_plan to the shape accepted by build_graph_from_task_plan."""
    loader_plan = {
        "plan_id": plan.get("plan_id"),
        "participants": copy.deepcopy(plan.get("participants", [])),
        "tasks": [],
        "relations": copy.deepcopy(plan.get("relations", [])),
        "global_constraints": copy.deepcopy(plan.get("global_constraints", {})),
        "explicit_constraints": copy.deepcopy(plan.get("explicit_constraints", [])),
    }
    for task in plan.get("tasks", []) or []:
        converted = {
            key: copy.deepcopy(value)
            for key, value in task.items()
            if key not in {"metadata"}
        }
        loader_plan["tasks"].append(converted)
    return loader_plan


def has_system_param_fields(obj: Any) -> list[str]:
    found: list[str] = []

    def walk(value: Any, path: str) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                next_path = f"{path}.{key}" if path else str(key)
                if key in SYSTEM_PARAM_FIELDS:
                    found.append(next_path)
                walk(item, next_path)
        elif isinstance(value, list):
            for idx, item in enumerate(value):
                walk(item, f"{path}[{idx}]")

    walk(obj, "")
    return found


def infer_difficulty(plan: dict[str, Any] | None, tags: list[str] | None = None) -> str:
    tags = tags or []
    if "hard" in tags:
        return "hard"
    if "medium" in tags:
        return "medium"
    if "easy" in tags:
        return "easy"
    if not plan:
        return "unknown"
    task_count = len(plan.get("tasks", []) or [])
    relation_types = {rel.get("type") or rel.get("relation") for rel in plan.get("relations", []) or []}
    explicit_count = len(plan.get("explicit_constraints", []) or [])
    if task_count <= 2 and explicit_count <= 1 and not (relation_types - {"sequence", None}):
        return "easy"
    if task_count <= 5:
        return "medium"
    return "hard"


def infer_language(text: str | None) -> str:
    if not text:
        return "en"
    has_ascii_word = any("A" <= ch <= "Z" or "a" <= ch <= "z" for ch in text)
    has_cjk = any("\u4e00" <= ch <= "\u9fff" for ch in text)
    if has_ascii_word and has_cjk:
        return "mixed"
    if has_cjk:
        return "zh"
    return "en"


def normalize_tags(*tag_groups: Iterable[str]) -> list[str]:
    tags: list[str] = []
    for group in tag_groups:
        for tag in group or []:
            text = str(tag)
            if text and text not in tags:
                tags.append(text)
    return tags
