"""
Run Phase 1 experiment presets from YAML.

Examples:
    python -m experiments.run_preset --list
    python -m experiments.run_preset phase1d_structured_ablation_latest --dry-run
    python -m experiments.run_preset phase1d_structured_ablation_latest
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any


DEFAULT_PRESET_FILE = Path("configs") / "experiment_presets.yaml"


class PresetError(RuntimeError):
    """User-facing preset configuration error."""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("preset_name", nargs="?", help="Preset name to run")
    parser.add_argument(
        "--preset-file",
        type=Path,
        default=DEFAULT_PRESET_FILE,
        help="YAML file containing experiment presets",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        dest="list_presets",
        help="List available presets and exit",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the resolved command without executing it",
    )
    parser.add_argument(
        "--set",
        action="append",
        default=[],
        metavar="PATH=VALUE",
        dest="overrides",
        help="Override a preset value, e.g. --set args.max_tokens=2400",
    )
    args = parser.parse_args()

    try:
        presets = _load_presets(args.preset_file)
        if args.list_presets:
            _print_presets(presets)
            return 0
        if not args.preset_name:
            parser.error("preset_name is required unless --list is used")

        preset = _select_preset(presets, args.preset_name)
        for override in args.overrides:
            _apply_override(preset, override)
        command = _build_command(preset)
    except PresetError as exc:
        print(f"[run_preset] {exc}", file=sys.stderr)
        return 2

    print("[run_preset] command:")
    print(_format_command(command))
    if args.dry_run:
        print("[run_preset] dry-run: command not executed")
        return 0
    return subprocess.run(command).returncode


def _load_presets(path: Path) -> dict[str, dict[str, Any]]:
    try:
        import yaml
    except ImportError as exc:
        raise PresetError(
            "Missing dependency: pyyaml. Install with: python -m pip install pyyaml"
        ) from exc

    if not path.exists():
        raise PresetError(f"Preset file not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise PresetError(f"Preset file must contain a mapping: {path}")
    presets = data.get("presets")
    if not isinstance(presets, dict) or not presets:
        raise PresetError(f"Preset file has no 'presets' mapping: {path}")
    return presets


def _print_presets(presets: dict[str, dict[str, Any]]) -> None:
    print("Available presets:")
    for name in sorted(presets):
        preset = presets[name] or {}
        module = preset.get("module", "")
        description = preset.get("description", "")
        suffix = f" - {description}" if description else ""
        print(f"  {name}: {module}{suffix}")


def _select_preset(
    presets: dict[str, dict[str, Any]],
    preset_name: str,
) -> dict[str, Any]:
    if preset_name not in presets:
        available = ", ".join(sorted(presets))
        raise PresetError(
            f"Preset not found: {preset_name!r}. Available presets: {available}"
        )
    preset = copy.deepcopy(presets[preset_name])
    if not isinstance(preset, dict):
        raise PresetError(f"Preset {preset_name!r} must be a mapping")
    return preset


def _apply_override(preset: dict[str, Any], assignment: str) -> None:
    if "=" not in assignment:
        raise PresetError(f"Invalid --set override, expected PATH=VALUE: {assignment}")
    path_text, value_text = assignment.split("=", 1)
    path = [part for part in path_text.strip().split(".") if part]
    if not path:
        raise PresetError(f"Invalid --set path: {assignment}")
    value = _parse_override_value(value_text)

    cursor: dict[str, Any] = preset
    for key in path[:-1]:
        existing = cursor.get(key)
        if existing is None:
            existing = {}
            cursor[key] = existing
        if not isinstance(existing, dict):
            raise PresetError(
                f"Cannot set {assignment!r}; {key!r} is not a mapping"
            )
        cursor = existing
    cursor[path[-1]] = value


def _parse_override_value(value_text: str) -> Any:
    try:
        import yaml
    except ImportError:
        return value_text
    return yaml.safe_load(value_text)


def _build_command(preset: dict[str, Any]) -> list[str]:
    module = preset.get("module")
    if not isinstance(module, str) or not module.strip():
        raise PresetError("Preset must define a non-empty 'module'")
    args = preset.get("args") or {}
    if not isinstance(args, dict):
        raise PresetError("Preset 'args' must be a mapping when provided")

    command = [sys.executable, "-m", module.strip()]
    command.extend(_args_to_cli(args))
    return command


def _args_to_cli(args: dict[str, Any]) -> list[str]:
    result: list[str] = []
    for key, raw_value in args.items():
        value = _resolve_value(raw_value)
        if value is None or value is False:
            continue
        flag = "--" + str(key).replace("_", "-")
        if value is True:
            result.append(flag)
        elif isinstance(value, list):
            result.append(flag)
            result.extend(str(item) for item in value)
        else:
            result.extend([flag, str(value)])
    return result


def _resolve_value(value: Any) -> Any:
    if isinstance(value, dict):
        if "latest_run" in value:
            return _resolve_latest_run_reference(value)
        raise PresetError(
            "Only latest_run references are supported for mapping argument values"
        )
    if isinstance(value, list):
        return [_resolve_value(item) for item in value]
    return value


def _resolve_latest_run_reference(value: dict[str, Any]) -> Any:
    root_text = str(value.get("latest_run") or "").strip()
    field = str(value.get("field") or "reports_dir")
    if not root_text:
        raise PresetError("latest_run reference requires a directory")
    root = Path(root_text)
    index_path = root / "latest_run.json"
    if not index_path.exists():
        raise PresetError(
            f"latest_run.json not found: {index_path}. "
            "Run the upstream preset first."
        )
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PresetError(f"Invalid JSON in {index_path}: {exc}") from exc
    if field not in index:
        available = ", ".join(sorted(index))
        raise PresetError(
            f"Field {field!r} not found in {index_path}. Available fields: {available}"
        )
    resolved = index[field]
    if resolved in (None, ""):
        raise PresetError(f"Field {field!r} in {index_path} is empty")
    return resolved


def _format_command(command: list[str]) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline(command)
    return shlex.join(command)


if __name__ == "__main__":
    raise SystemExit(main())
