"""
tools/convert_facilities_to_yaml.py
python -m tools.convert_facilities_to_yaml --input data/facilities_utm.json --output configs/environment_facilities.yaml --scenario-id scenario_facilities_utm --origin-ref hq_1
Convert facilities_utm.json into configs/environment_facilities.yaml.

Input format:
{
  "facilities_str": {
    "hq_1": [utm_x_m, utm_y_m],
    "ua_1": [utm_x_m, utm_y_m],
    ...
  },
  "defence_rings": {
    "RING1": {
      "lngs": [utm_x_m, ...],
      "lats": [utm_y_m, ...]
    }
  }
}

Usage:
    python tools/convert_facilities_to_yaml.py `
        --input data/facilities_utm.json `
        --output configs/environment_facilities.yaml `
        --scenario-id scenario_facilities_utm `
        --origin-ref hq_1

Or in one line:
    python tools/convert_facilities_to_yaml.py --input data/facilities_utm.json --output configs/environment_facilities.yaml --scenario-id scenario_facilities_utm --origin-ref hq_1
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError as exc:
    raise RuntimeError(
        "Missing dependency: pyyaml. Install with: python -m pip install pyyaml"
    ) from exc


def load_json(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def dump_yaml(data: dict[str, Any], path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(
            data,
            f,
            allow_unicode=True,
            sort_keys=False,
            indent=2,
        )


def to_relative_km(
    utm_x_m: float,
    utm_y_m: float,
    origin_x_m: float,
    origin_y_m: float,
    ndigits: int = 6,
) -> dict[str, float]:
    """
    Convert absolute UTM coordinates in meters to local relative coordinates in km.
    """
    return {
        "x": round((float(utm_x_m) - float(origin_x_m)) / 1000.0, ndigits),
        "y": round((float(utm_y_m) - float(origin_y_m)) / 1000.0, ndigits),
    }


def infer_origin(
    facilities: dict[str, list[float]],
    origin_ref: str | None,
) -> tuple[str, float, float]:
    """
    Select origin point.

    Priority:
    1. origin_ref if provided;
    2. hq_1 if exists;
    3. mean center of all facilities.
    """
    if origin_ref:
        if origin_ref not in facilities:
            raise ValueError(
                f"origin_ref '{origin_ref}' not found in facilities_str. "
                f"Available keys: {list(facilities.keys())}"
            )
        ox, oy = facilities[origin_ref]
        return origin_ref, float(ox), float(oy)

    if "hq_1" in facilities:
        ox, oy = facilities["hq_1"]
        return "hq_1", float(ox), float(oy)

    if not facilities:
        raise ValueError("facilities_str is empty; cannot infer origin")

    xs = [float(v[0]) for v in facilities.values()]
    ys = [float(v[1]) for v in facilities.values()]
    return "mean_center", sum(xs) / len(xs), sum(ys) / len(ys)


def facility_to_actor_id(name: str) -> str:
    """
    Map ua_1 -> fleet_1, ua_2 -> fleet_2, etc.
    """
    if name.startswith("ua_"):
        suffix = name[len("ua_") :]
        return f"fleet_{suffix}"
    return name


def classify_facility(name: str) -> str:
    """
    Classify facilities into initial_positions or target_points.

    Rule:
    - ua_* -> initial position of fleet_*
    - all others -> target_points
    """
    if name.startswith("ua_"):
        return "initial_position"
    return "target_point"


def convert_facilities_utm_to_environment(
    facilities_data: dict[str, Any],
    *,
    scenario_id: str,
    origin_ref: str | None = None,
    description: str = "Converted from facilities UTM JSON",
    default_ring_type: str = "threat_zone",
    default_threat_level: int = 3,
    default_speed_penalty: float = 1.0,
    hard_deadline: float | None = None,
) -> dict[str, Any]:
    """
    Convert facilities UTM JSON into environment_config-style YAML data.

    Notes:
    - UTM input unit is assumed to be meter.
    - Output x/y unit is km, relative to origin.
    - defence_rings are converted into polygon threat_zones by default.
    """
    facilities = facilities_data.get("facilities_str", {})
    defence_rings = facilities_data.get("defence_rings", {})

    if not isinstance(facilities, dict) or not facilities:
        raise ValueError("Input JSON must contain non-empty 'facilities_str'")

    origin_name, origin_x_m, origin_y_m = infer_origin(facilities, origin_ref)

    initial_positions: dict[str, Any] = {}
    target_points: dict[str, Any] = {}

    for name, coords in facilities.items():
        if not isinstance(coords, list) or len(coords) != 2:
            raise ValueError(f"Invalid coordinate for facility '{name}': {coords}")

        utm_x_m, utm_y_m = float(coords[0]), float(coords[1])
        local = to_relative_km(utm_x_m, utm_y_m, origin_x_m, origin_y_m)

        common = {
            "x": local["x"],
            "y": local["y"],
            "source_ref": name,
            "source_utm_m": {
                "x": utm_x_m,
                "y": utm_y_m,
            },
        }

        category = classify_facility(name)

        if category == "initial_position":
            actor_id = facility_to_actor_id(name)
            initial_positions[actor_id] = {
                **common,
                "description": f"Initial position converted from {name}",
            }
        else:
            target_points[name] = {
                **common,
                "description": f"Facility converted from {name}",
                "source_type": "facility",
                "threat_level": None,
                "defense_type": None,
            }

    no_fly_zones: list[dict[str, Any]] = []
    threat_zones: list[dict[str, Any]] = []

    for ring_id, ring in defence_rings.items():
        xs = ring.get("lngs", [])
        ys = ring.get("lats", [])

        if len(xs) != len(ys):
            raise ValueError(
                f"Invalid defence ring '{ring_id}': len(lngs) != len(lats)"
            )

        vertices = []
        for utm_x_m, utm_y_m in zip(xs, ys):
            local = to_relative_km(
                float(utm_x_m),
                float(utm_y_m),
                origin_x_m,
                origin_y_m,
            )
            vertices.append(
                {
                    "x": local["x"],
                    "y": local["y"],
                    "source_utm_m": {
                        "x": float(utm_x_m),
                        "y": float(utm_y_m),
                    },
                }
            )

        zone = {
            "id": ring_id,
            "type": "polygon",
            "vertices": vertices,
            "description": f"Converted defence ring {ring_id}",
            "source_type": "defence_ring",
        }

        if default_ring_type == "no_fly_zone":
            no_fly_zones.append(zone)
        else:
            threat_zones.append(
                {
                    **zone,
                    "threat_level": default_threat_level,
                    "speed_penalty": default_speed_penalty,
                }
            )

    scenario = {
        "description": description,
        "time_unit": "分钟",
        "coordinate_unit": "km",
        "coordinate_system": {
            "type": "utm_relative",
            "source_crs": "UTM",
            "source_unit": "m",
            "output_unit": "km",
            "origin_ref": origin_name,
            "origin_utm_m": {
                "x": origin_x_m,
                "y": origin_y_m,
            },
            "x_axis": "east",
            "y_axis": "north",
        },
        "initial_positions": initial_positions,
        "target_points": target_points,
        "rendezvous_points": {},
        "no_fly_zones": no_fly_zones,
        "threat_zones": threat_zones,
        "time_constraints": {
            "mission_start": 0,
            "hard_deadline": hard_deadline,
        },
    }

    return {
        "scenarios": {
            scenario_id: scenario,
        }
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert facilities UTM JSON to environment YAML"
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Path to facilities_utm.json",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Path to output environment_facilities.yaml",
    )
    parser.add_argument(
        "--scenario-id",
        default="scenario_facilities_utm",
        help="Scenario ID written into environment YAML",
    )
    parser.add_argument(
        "--origin-ref",
        default=None,
        help="Facility key used as origin, e.g. hq_1. If omitted, hq_1 or mean center is used.",
    )
    parser.add_argument(
        "--ring-type",
        choices=["threat_zone", "no_fly_zone"],
        default="threat_zone",
        help="How to convert defence_rings",
    )
    parser.add_argument(
        "--speed-penalty",
        type=float,
        default=1.0,
        help="Default speed penalty for defence rings converted to threat_zones. 1.0 means no penalty.",
    )
    parser.add_argument(
        "--threat-level",
        type=int,
        default=3,
        help="Default threat level for defence rings converted to threat_zones.",
    )
    parser.add_argument(
        "--hard-deadline",
        type=float,
        default=None,
        help="Optional hard deadline in minutes.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    facilities_data = load_json(args.input)

    env_data = convert_facilities_utm_to_environment(
        facilities_data,
        scenario_id=args.scenario_id,
        origin_ref=args.origin_ref,
        default_ring_type=args.ring_type,
        default_threat_level=args.threat_level,
        default_speed_penalty=args.speed_penalty,
        hard_deadline=args.hard_deadline,
    )

    dump_yaml(env_data, args.output)

    scenario = env_data["scenarios"][args.scenario_id]
    print("[OK] Converted facilities UTM JSON to environment YAML")
    print(f"  input        : {args.input}")
    print(f"  output       : {args.output}")
    print(f"  scenario_id  : {args.scenario_id}")
    print(f"  origin_ref   : {scenario['coordinate_system']['origin_ref']}")
    print(f"  initial_positions: {len(scenario['initial_positions'])}")
    print(f"  target_points     : {len(scenario['target_points'])}")
    print(f"  no_fly_zones      : {len(scenario['no_fly_zones'])}")
    print(f"  threat_zones      : {len(scenario['threat_zones'])}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())