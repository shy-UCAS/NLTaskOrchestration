"""
gcjp/environment_model.py

Lightweight environment model for task-plan validation.

Current scope:
1. Load environment_config.yaml / environment_facilities.yaml.
2. Resolve scenario by scenario_id.
3. Validate actor and target references used in a standardized task plan.
4. Keep extension interfaces for future distance / feasibility analysis.

This module intentionally does NOT perform complex feasibility analysis now.
It is designed as a thin environment reference layer before NL → structured task extraction.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


class EnvironmentModelError(ValueError):
    """Base error for environment model problems."""


class ScenarioNotFoundError(EnvironmentModelError):
    """Raised when scenario_id does not exist in environment config."""


class EnvironmentReferenceError(EnvironmentModelError):
    """Raised when actor/target references cannot be resolved."""


@dataclass
class EnvironmentValidationResult:
    passed: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def add_error(self, msg: str) -> None:
        self.errors.append(msg)
        self.passed = False

    def add_warning(self, msg: str) -> None:
        self.warnings.append(msg)


def load_environment_config(path: str | Path) -> dict[str, Any]:
    """
    Load environment YAML.

    Expected format:
    scenarios:
      scenario_id:
        initial_positions: ...
        target_points: ...
        rendezvous_points: ...
        no_fly_zones: ...
        threat_zones: ...
    """
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError(
            "Missing dependency: pyyaml. Install with: python -m pip install pyyaml"
        ) from exc

    path = Path(path)
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if data is None:
        raise EnvironmentModelError(f"Environment config is empty: {path}")

    if not isinstance(data, dict):
        raise EnvironmentModelError(f"Environment config root must be a dict: {path}")

    if "scenarios" not in data:
        raise EnvironmentModelError(
            f"Environment config must contain top-level key 'scenarios': {path}"
        )

    return data


def get_scenario(env_config: dict[str, Any], scenario_id: str) -> dict[str, Any]:
    """
    Get scenario config by scenario_id.
    """
    scenarios = env_config.get("scenarios", {})

    if scenario_id not in scenarios:
        raise ScenarioNotFoundError(
            f"scenario_id '{scenario_id}' not found in environment config. "
            f"Available scenarios: {list(scenarios.keys())}"
        )

    return scenarios[scenario_id]


def get_known_location_refs(scenario: dict[str, Any]) -> set[str]:
    """
    Collect all known location references in a scenario.
    """
    refs: set[str] = set()

    refs.update((scenario.get("initial_positions") or {}).keys())
    refs.update((scenario.get("target_points") or {}).keys())
    refs.update((scenario.get("rendezvous_points") or {}).keys())

    return refs


def get_actor_refs(scenario: dict[str, Any]) -> set[str]:
    """
    Get actor IDs that have initial positions.
    """
    return set((scenario.get("initial_positions") or {}).keys())


def is_symbolic_or_dynamic_target(target: str) -> bool:
    """
    Some targets in task plans may be semantic targets instead of map locations.

    Examples:
    - enemy_main_group
    - enemy_uav_group
    - hostile_target
    - moving_target_1

    These can be allowed during early NL parsing and resolved later.
    """
    if not isinstance(target, str):
        return False

    prefixes = (
        "enemy_",
        "hostile_",
        "unknown_",
        "moving_",
        "dynamic_",
    )

    return target.startswith(prefixes)


def resolve_location(
    scenario: dict[str, Any],
    ref: str,
) -> dict[str, float]:
    """
    Resolve a location reference to x/y coordinate.

    Supports:
    - initial_positions
    - target_points
    - rendezvous_points

    Returns:
        {"x": float, "y": float}

    Raises:
        EnvironmentReferenceError if ref is not found or coordinate is invalid.
    """
    containers = [
        ("initial_positions", scenario.get("initial_positions") or {}),
        ("target_points", scenario.get("target_points") or {}),
        ("rendezvous_points", scenario.get("rendezvous_points") or {}),
    ]

    for container_name, container in containers:
        if ref in container:
            point = container[ref]
            try:
                return {
                    "x": float(point["x"]),
                    "y": float(point["y"]),
                }
            except Exception as exc:
                raise EnvironmentReferenceError(
                    f"Location ref '{ref}' found in {container_name}, "
                    f"but x/y coordinate is invalid: {point}"
                ) from exc

    raise EnvironmentReferenceError(
        f"Location ref '{ref}' not found in scenario. "
        f"Known refs: {sorted(get_known_location_refs(scenario))}"
    )


def euclidean_distance_km(
    p1: dict[str, float],
    p2: dict[str, float],
) -> float:
    """
    Compute Euclidean distance in km.

    This assumes x/y are already local planar coordinates in km.
    """
    return math.sqrt((p2["x"] - p1["x"]) ** 2 + (p2["y"] - p1["y"]) ** 2)


def estimate_straight_line_metrics(
    scenario: dict[str, Any],
    *,
    from_ref: str,
    to_ref: str,
    cruise_speed_kmh: float,
    energy_per_km: float = 0.2,
) -> dict[str, float]:
    """
    Minimal placeholder for future feasibility analysis.

    Current behavior:
    - Straight-line Euclidean distance only.
    - No no-fly-zone detour.
    - No threat-zone penalty.
    - No dynamic obstacle.

    Returns:
        {
          "distance_km": ...,
          "min_duration": ...,  # minutes
          "energy_cost": ...,
        }
    """
    if cruise_speed_kmh <= 0:
        raise EnvironmentModelError(
            f"cruise_speed_kmh must be positive, got {cruise_speed_kmh}"
        )

    p1 = resolve_location(scenario, from_ref)
    p2 = resolve_location(scenario, to_ref)

    distance_km = euclidean_distance_km(p1, p2)
    min_duration = distance_km / cruise_speed_kmh * 60.0
    energy_cost = distance_km * energy_per_km

    return {
        "distance_km": distance_km,
        "min_duration": min_duration,
        "energy_cost": energy_cost,
    }


def validate_environment_refs(
    plan: dict[str, Any],
    scenario: dict[str, Any],
    *,
    strict_targets: bool = False,
    allow_symbolic_targets: bool = True,
) -> EnvironmentValidationResult:
    """
    Validate environment references used by a standardized task plan.

    Checks:
    1. participant actor_id exists in scenario.initial_positions
    2. task.actor exists in participants
    3. task.actor has initial position
    4. task.target exists in known map refs, unless symbolic targets are allowed

    This function does NOT check route feasibility.
    """
    result = EnvironmentValidationResult(passed=True)

    initial_actor_refs = get_actor_refs(scenario)
    known_locations = get_known_location_refs(scenario)

    participants = plan.get("participants", [])
    tasks = plan.get("tasks", [])

    participant_ids = {p.get("actor_id") for p in participants if p.get("actor_id")}

    # 1. Validate participants
    for actor_id in sorted(participant_ids):
        if actor_id not in initial_actor_refs:
            result.add_error(
                f"participant actor_id '{actor_id}' has no initial position "
                f"in environment scenario. Available actors: {sorted(initial_actor_refs)}"
            )

    # 2. Validate tasks
    for task in tasks:
        task_id = task.get("task_id", "<unknown_task>")
        actor = task.get("actor")
        target = task.get("target")
        action = task.get("action")

        if not actor:
            result.add_error(f"task '{task_id}' missing actor")
            continue

        if actor not in participant_ids:
            result.add_error(
                f"task '{task_id}' uses actor '{actor}', "
                f"but it is not declared in participants: {sorted(participant_ids)}"
            )

        if actor not in initial_actor_refs:
            result.add_error(
                f"task '{task_id}' actor '{actor}' has no initial position "
                f"in environment scenario"
            )

        if not target:
            result.add_warning(f"task '{task_id}' has empty target")
            continue

        if target in known_locations:
            continue

        if allow_symbolic_targets and is_symbolic_or_dynamic_target(target):
            result.add_warning(
                f"task '{task_id}' target '{target}' is treated as symbolic/dynamic target"
            )
            continue

        message = (
            f"task '{task_id}' target '{target}' not found in environment scenario. "
            f"Known location refs: {sorted(known_locations)}"
        )

        if strict_targets:
            result.add_error(message)
        else:
            result.add_warning(message)

    return result


def validate_task_plan_environment(
    plan: dict[str, Any],
    env_config: dict[str, Any],
    *,
    strict_targets: bool = False,
    allow_symbolic_targets: bool = True,
) -> EnvironmentValidationResult:
    """
    High-level validation entry.

    Uses plan["scenario_id"] to find scenario, then validates references.
    """
    scenario_id = plan.get("scenario_id")
    if not scenario_id:
        return EnvironmentValidationResult(
            passed=False,
            errors=["task plan missing scenario_id"],
            warnings=[],
        )

    try:
        scenario = get_scenario(env_config, scenario_id)
    except ScenarioNotFoundError as exc:
        return EnvironmentValidationResult(
            passed=False,
            errors=[str(exc)],
            warnings=[],
        )

    return validate_environment_refs(
        plan,
        scenario,
        strict_targets=strict_targets,
        allow_symbolic_targets=allow_symbolic_targets,
    )