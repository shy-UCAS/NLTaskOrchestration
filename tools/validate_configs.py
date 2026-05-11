"""
tools/validate_configs.py

Validate basic readability and required top-level keys of YAML config files.

Usage:
    python tools/validate_configs.py
"""

from __future__ import annotations

from pathlib import Path

try:
    import yaml
except ImportError as exc:
    raise RuntimeError(
        "Missing dependency: pyyaml. Install with: python -m pip install pyyaml"
    ) from exc


ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "configs"


REQUIRED_FILES = {
    "capability_model.yaml": ["fleets"],
    "action_templates.yaml": ["actions"],
    "environment_config.yaml": ["scenarios"],
}


def load_yaml(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"YAML root must be a mapping: {path}")
    return data


def main() -> int:
    print("=" * 60)
    print("Config Validation")
    print("=" * 60)

    all_passed = True

    for filename, required_keys in REQUIRED_FILES.items():
        path = CONFIG_DIR / filename
        print(f"\nChecking: {path}")

        if not path.exists():
            print(f"  [ERROR] File not found")
            all_passed = False
            continue

        try:
            data = load_yaml(path)
        except Exception as e:
            print(f"  [ERROR] Failed to load YAML: {e}")
            all_passed = False
            continue

        print("  [OK] YAML loaded")

        for key in required_keys:
            if key not in data:
                print(f"  [ERROR] Missing top-level key: {key}")
                all_passed = False
            else:
                value = data[key]
                if isinstance(value, dict):
                    print(f"  [OK] {key}: {len(value)} item(s)")
                elif isinstance(value, list):
                    print(f"  [OK] {key}: {len(value)} item(s)")
                else:
                    print(f"  [OK] {key}: {type(value).__name__}")

    print("\n" + "=" * 60)
    if all_passed:
        print("[VALID] All config files passed basic validation")
        return 0

    print("[INVALID] Some config files failed validation")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())