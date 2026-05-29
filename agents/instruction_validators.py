"""Deterministic contract checks for Phase 1F instruction normalization."""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ACTION_LEXICON_PATH = PROJECT_ROOT / "configs" / "action_lexicon.yaml"
DEFAULT_CAPABILITY_MODEL_PATH = PROJECT_ROOT / "configs" / "capability_model.yaml"

FLEET_ID_RE = re.compile(r"^fleet_\d+$")
TARGET_ID_RE = re.compile(
    r"^(?:area|target|site|node|point|defense|corridor|sector|ridge|hq|"
    r"radar|rv|nfz|tz)_[A-Za-z0-9][A-Za-z0-9_]*$"
)
ALLOWED_RELATION_TYPES = {
    "sequence",
    "parallel",
    "sync",
    "conditional",
    "fork",
    "join",
}


@dataclass(frozen=True)
class ActionIssue:
    span: str
    reason: str
    field: str = "action"


@dataclass(frozen=True)
class ContractViolation:
    field: str
    span: str
    reason: str
    source: str

    def as_ambiguity(self) -> dict[str, str]:
        return {
            "span": self.span,
            "reason": self.reason,
            "field": self.field,
        }

    def as_record(self) -> dict[str, str]:
        return {
            "field": self.field,
            "span": self.span,
            "reason": self.reason,
            "source": self.source,
        }


@dataclass(frozen=True)
class ContractValidationResult:
    violations: list[ContractViolation]

    @property
    def ok(self) -> bool:
        return not self.violations

    @property
    def missing_fields(self) -> list[str]:
        fields: list[str] = []
        seen: set[str] = set()
        for violation in self.violations:
            if violation.field not in seen:
                seen.add(violation.field)
                fields.append(violation.field)
        return fields

    @property
    def ambiguities(self) -> list[dict[str, str]]:
        return [violation.as_ambiguity() for violation in self.violations]

    def as_records(self) -> list[dict[str, str]]:
        return [violation.as_record() for violation in self.violations]


class ActionLexicon:
    def __init__(self, raw: dict[str, Any]):
        self.standard_actions = {
            str(action) for action in raw.get("standard_actions", [])
        }
        actions = raw.get("actions") or {}
        self.action_phrases: dict[str, set[str]] = {}
        self.phrase_actions: dict[str, set[str]] = {}
        if isinstance(actions, dict):
            for action, spec in actions.items():
                action_name = str(action)
                phrases = (spec or {}).get("phrases", [])
                if not isinstance(phrases, list):
                    continue
                for phrase in phrases:
                    if not isinstance(phrase, str) or not phrase:
                        continue
                    self.action_phrases.setdefault(action_name, set()).add(phrase)
                    self.phrase_actions.setdefault(phrase, set()).add(action_name)

        self.ambiguous_phrases = _load_phrase_specs(
            raw.get("ambiguous_phrases"),
        )
        self.unresolvable_phrases = _load_phrase_specs(
            raw.get("unresolvable_phrases"),
        )

    def resolve_action(self, span: str) -> dict[str, Any]:
        text = span.strip()
        actions = sorted(self.phrase_actions.get(text, set()))
        if len(actions) == 1:
            return {"status": "unique", "action": actions[0], "candidates": actions}
        if len(actions) > 1:
            return {"status": "ambiguous", "action": None, "candidates": actions}
        for item in self.ambiguous_phrases:
            if text == item["phrase"]:
                return {
                    "status": "ambiguous",
                    "action": None,
                    "candidates": item.get("candidates", []),
                    "reason": item["reason"],
                }
        for item in self.unresolvable_phrases:
            if text == item["phrase"]:
                return {
                    "status": "unknown",
                    "action": None,
                    "candidates": [],
                    "reason": item["reason"],
                }
        return {"status": "unknown", "action": None, "candidates": []}

    def detect_action_issues(self, raw_instruction: str) -> list[ActionIssue]:
        covered = _covered_ranges(raw_instruction, self.phrase_actions)
        issues: list[ActionIssue] = []
        seen: set[tuple[str, int]] = set()
        for item in self.ambiguous_phrases + self.unresolvable_phrases:
            phrase = item["phrase"]
            for start in _find_all(raw_instruction, phrase):
                end = start + len(phrase)
                if _range_covered(start, end, covered):
                    continue
                key = (phrase, start)
                if key in seen:
                    continue
                seen.add(key)
                issues.append(ActionIssue(span=phrase, reason=item["reason"]))
        return issues


def validate_normalization_contract(
    parsed_output: dict[str, Any] | None,
    *,
    raw_instruction: str,
) -> ContractValidationResult:
    """Validate a candidate normalization against executable 1F contracts."""
    violations: list[ContractViolation] = []
    if not isinstance(parsed_output, dict):
        return ContractValidationResult([
            ContractViolation(
                field="parsed_output",
                span="parsed_output",
                reason="normalizer did not return a JSON object",
                source="schema",
            )
        ])

    resolved = parsed_output.get("resolved_fields")
    if not isinstance(resolved, dict):
        return ContractValidationResult([
            ContractViolation(
                field="resolved_fields",
                span="resolved_fields",
                reason="resolved_fields must be an object",
                source="schema",
            )
        ])

    lexicon = load_action_lexicon()
    _validate_actions(resolved, raw_instruction, lexicon, violations)
    _validate_actors(resolved, load_known_actors(), violations)
    _validate_targets(resolved, violations)
    _validate_relations(resolved, violations)
    return ContractValidationResult(_dedupe_violations(violations))


def resolve_action(span: str) -> dict[str, Any]:
    return load_action_lexicon().resolve_action(span)


@lru_cache(maxsize=1)
def load_action_lexicon(
    path: str | Path = DEFAULT_ACTION_LEXICON_PATH,
) -> ActionLexicon:
    return ActionLexicon(_load_yaml(Path(path)))


@lru_cache(maxsize=1)
def load_known_actors(
    path: str | Path = DEFAULT_CAPABILITY_MODEL_PATH,
) -> set[str]:
    raw = _load_yaml(Path(path))
    fleets = raw.get("fleets") or {}
    if not isinstance(fleets, dict):
        return set()
    return {str(actor) for actor in fleets}


def _validate_actions(
    resolved: dict[str, Any],
    raw_instruction: str,
    lexicon: ActionLexicon,
    violations: list[ContractViolation],
) -> None:
    for task in _iter_tasks(resolved):
        action = task.get("action")
        if not isinstance(action, str) or action not in lexicon.standard_actions:
            violations.append(ContractViolation(
                field="action",
                span=str(action),
                reason="task action is not in the declared standard action set",
                source="action_lexicon",
            ))

    for issue in lexicon.detect_action_issues(raw_instruction):
        violations.append(ContractViolation(
            field=issue.field,
            span=issue.span,
            reason=issue.reason,
            source="action_lexicon",
        ))


def _validate_actors(
    resolved: dict[str, Any],
    known_actors: set[str],
    violations: list[ContractViolation],
) -> None:
    values: list[Any] = []
    assigned = resolved.get("assigned_actors")
    if isinstance(assigned, list):
        values.extend(assigned)
    elif assigned is not None:
        values.append(assigned)
    values.extend(task.get("actor") for task in _iter_tasks(resolved))

    for actor in values:
        if not isinstance(actor, str) or not FLEET_ID_RE.fullmatch(actor):
            violations.append(ContractViolation(
                field="assigned_actors",
                span=str(actor),
                reason="actor must be a concrete fleet_N identifier",
                source="entity_validator",
            ))
            continue
        if known_actors and actor not in known_actors:
            violations.append(ContractViolation(
                field="assigned_actors",
                span=actor,
                reason="actor is not defined in capability_model",
                source="entity_validator",
            ))


def _validate_targets(
    resolved: dict[str, Any],
    violations: list[ContractViolation],
) -> None:
    for task in _iter_tasks(resolved):
        target = task.get("target")
        if not isinstance(target, str) or not TARGET_ID_RE.fullmatch(target):
            violations.append(ContractViolation(
                field="target",
                span=str(target),
                reason=(
                    "target must be a concrete declared ID such as "
                    "area_X, target_X, site_X, node_X, point_X, or defense_X"
                ),
                source="entity_validator",
            ))


def _validate_relations(
    resolved: dict[str, Any],
    violations: list[ContractViolation],
) -> None:
    tasks = list(_iter_tasks(resolved))
    task_ids = {
        task.get("task_id") for task in tasks
        if isinstance(task.get("task_id"), str)
    }
    relations = resolved.get("relations")
    if len(tasks) > 1 and not relations:
        violations.append(ContractViolation(
            field="relation",
            span="relations",
            reason="multiple tasks require an explicit relation",
            source="schema",
        ))
        return
    if relations is None:
        return
    if not isinstance(relations, list):
        violations.append(ContractViolation(
            field="relation",
            span="relations",
            reason="relations must be a list",
            source="schema",
        ))
        return
    for relation in relations:
        if not isinstance(relation, dict):
            violations.append(ContractViolation(
                field="relation",
                span=str(relation),
                reason="relation entries must be objects",
                source="schema",
            ))
            continue
        relation_type = relation.get("type")
        if relation_type not in ALLOWED_RELATION_TYPES:
            violations.append(ContractViolation(
                field="relation",
                span=str(relation_type),
                reason="relation type is not in the declared relation set",
                source="schema",
            ))
        for endpoint in ("from", "to"):
            ref = relation.get(endpoint)
            if task_ids and ref not in task_ids:
                violations.append(ContractViolation(
                    field="relation",
                    span=str(ref),
                    reason=f"relation {endpoint!r} does not reference a task_id",
                    source="schema",
                ))


def _iter_tasks(resolved: dict[str, Any]) -> list[dict[str, Any]]:
    tasks = resolved.get("tasks")
    if not isinstance(tasks, list):
        return []
    return [task for task in tasks if isinstance(task, dict)]


def _covered_ranges(
    text: str,
    phrase_actions: dict[str, set[str]],
) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    for phrase in phrase_actions:
        for start in _find_all(text, phrase):
            ranges.append((start, start + len(phrase)))
    return ranges


def _range_covered(
    start: int,
    end: int,
    ranges: list[tuple[int, int]],
) -> bool:
    return any(range_start <= start and end <= range_end for range_start, range_end in ranges)


def _find_all(text: str, phrase: str) -> list[int]:
    starts: list[int] = []
    start = 0
    while True:
        idx = text.find(phrase, start)
        if idx < 0:
            return starts
        starts.append(idx)
        start = idx + 1


def _load_phrase_specs(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    specs: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        phrase = item.get("phrase")
        if not isinstance(phrase, str) or not phrase:
            continue
        specs.append({
            "phrase": phrase,
            "reason": str(item.get("reason") or "action is not uniquely resolvable"),
            "candidates": list(item.get("candidates") or []),
        })
    return specs


def _dedupe_violations(
    violations: list[ContractViolation],
) -> list[ContractViolation]:
    deduped: list[ContractViolation] = []
    seen: set[tuple[str, str, str, str]] = set()
    for violation in violations:
        key = (
            violation.field,
            violation.span,
            violation.reason,
            violation.source,
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(violation)
    return deduped


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - local setup issue
        raise RuntimeError("PyYAML is required for instruction validators") from exc
    if not path.exists():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        return {}
    return raw
