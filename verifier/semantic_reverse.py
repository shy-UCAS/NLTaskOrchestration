"""Reverse-verbalization semantic consistency checker.

Closes the one gap the structural guards (schema / ref-check / Z3) leave open: nothing
verifies that a ``canonical_task_plan`` actually *means* what its ``standard_instruction``
says. Z3 only checks feasibility, so a plan that mislabels a ``sequence`` as ``parallel``,
picks the wrong (but valid) target, or drops a deadline can be schema-valid, ref-valid and
Z3-sat while silently contradicting the instruction.

This is the engine the stubbed ``Layer4SemanticVerifier`` in ``verifier/pipeline.py`` was
reserved for; it is implemented at the dataset layer first because that is where the
(instruction, plan) pair exists. Two steps, three components:

    verbalize(plan)            -> readable NL rendering of the plan      (deterministic)
    tier1_check(plan, nl)      -> deterministic lexical/entity drift      (no LLM)
    tier2_check(plan, nl, ...) -> LLM semantic judge                      (opt-in, advisory)

The checker is **advisory only**: it reports discrepancies, never mutates a case and never
blocks a build. Tier-1 favours precision (flag only when confident; softer signals are
labelled ``soft``); recall on free-form text is deferred to Tier-2.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from gcjp.api_spec import RELATION_ALIASES, VALID_RELATION_TYPES

# Action vocabulary lives in configs/action_templates.yaml; surface synonyms here so Tier-1
# can do a (soft) morphological presence check without re-reading YAML on every call.
_ACTION_SYNONYMS: dict[str, tuple[str, ...]] = {
    "reconnaissance": ("reconnaissance", "reconnoiter", "reconnoitre", "recon", "scout"),
    "strike": ("strike", "strikes", "struck", "attack"),
    "breakthrough": ("breakthrough", "break through", "breach"),
    "fly_to": ("fly to", "flies to", "fly", "transit", "move to"),
    "rendezvous": ("rendezvous", "regroup", "assemble", "meet"),
    "standby": ("standby", "stand by", "hold", "wait"),
    "jam": ("jam", "jams", "jamming", "jammed"),
    "intercept": ("intercept", "intercepts", "interception"),
    "track": ("track", "tracks", "tracking", "trail"),
}

# NL keyword families -> the relation type they imply. Conservative on purpose: bare "if"/
# "when" are excluded from condition_trigger to keep precision high.
_RELATION_KEYWORDS: dict[str, tuple[str, ...]] = {
    "sequence": ("then", "after", "afterwards", "subsequently", "followed by",
                 "先", "随后", "接着", "之后"),
    "parallel": ("parallel", "simultaneously", "concurrently", "at the same time",
                 "in parallel", "同时"),
    "sync": ("synchronize", "synchronized", "synchronously", "in sync", "同步"),
    "condition_trigger": ("once", "upon", "gated on", "triggered", "一旦", "确认后"),
}

# Resource / capability prose lives in configs, NOT in the plan. These lexemes must never be
# treated as "missing from the plan" (the dominant Tier-1 false-positive class). Tier-1 only
# checks plan->NL presence, so this set is a documented guard rather than an active filter.
_RESOURCE_LEXEMES: frozenset[str] = frozenset({
    "ammunition", "ammo", "energy", "fuel", "capability", "capable", "carries",
    "carry", "budget", "弹药", "能量", "能力",
})


@dataclass
class Discrepancy:
    kind: str                       # relation_mismatch | target_absent | actor_absent | ...
    severity: str                   # "strong" | "soft"
    locus: str                      # task_id / "t1->t2" / "" depending on kind
    nl_implies: str                 # what the instruction suggests
    plan_has: str                   # what the plan encodes
    detail: str
    tier: str                       # "lexical" | "llm"


@dataclass
class CaseReport:
    sample_id: str
    consistent: bool
    discrepancies: list[Discrepancy] = field(default_factory=list)
    verbalized: str = ""
    llm_skipped: bool = False       # True when Tier-2 was requested but unavailable


# --------------------------------------------------------------------------- #
# Component 1: verbalize (deterministic, no LLM)
# --------------------------------------------------------------------------- #
def verbalize(plan: Optional[dict[str, Any]]) -> str:
    """Render a canonical_task_plan back into a readable, system-param-free description.

    Renders from the plan dict (NOT a BuiltGraph) so injected duration/energy/ammo never
    leak into the rendering and cause false drift against the instruction.
    """
    if not plan:
        return "(no canonical_task_plan)"

    lines: list[str] = []
    for task in plan.get("tasks", []) or []:
        line = (
            f"{task.get('actor', '?')} performs {task.get('action', '?')} "
            f"on {task.get('target', '?')} as {task.get('task_id', '?')}"
        )
        if task.get("condition"):
            line += f" [condition: {task['condition']}]"
        tw = task.get("time_window") or {}
        bits = [f"{k}={tw[k]}" for k in ("earliest", "latest", "deadline") if tw.get(k) is not None]
        if bits:
            line += f" [time_window: {', '.join(bits)}]"
        lines.append(line)

    for rel in plan.get("relations", []) or []:
        rtype = rel.get("type") or rel.get("relation") or "sequence"
        line = f"{rel.get('source', '?')} --{rtype}--> {rel.get('target', '?')}"
        if rel.get("condition"):
            line += f" [condition: {rel['condition']}]"
        if rel.get("sync_tolerance") is not None:
            line += f" [tolerance: {rel['sync_tolerance']}]"
        lines.append(line)

    for con in plan.get("explicit_constraints", []) or []:
        ctype = con.get("type", "?")
        if ctype == "group_sync":
            lines.append(
                f"group_sync over {con.get('task_ids')} "
                f"(mode={con.get('mode')}, tolerance={con.get('tolerance')})"
            )
        elif ctype == "physical_feasibility":
            lines.append(
                f"physical_feasibility {con.get('task_id')}: "
                f"{con.get('distance_km')}km @ {con.get('actor_speed_kmh')}km/h"
            )
        else:
            lines.append(f"constraint[{ctype}]: {con}")

    return "\n".join(lines) if lines else "(empty plan)"


# --------------------------------------------------------------------------- #
# Component 2: tier1_check (deterministic lexical / entity cross-check)
# --------------------------------------------------------------------------- #
def _normalize(text: Optional[str]) -> str:
    """Lower-case, unify ``_``/space, drop punctuation, pad with spaces for phrase matching."""
    text = (text or "").lower().replace("_", " ")
    text = re.sub(r"[^a-z0-9一-鿿]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return f" {text} "


def _contains(normalized_hay: str, value: str) -> bool:
    norm_val = _normalize(value).strip()
    if not norm_val:
        return True
    return f" {norm_val} " in normalized_hay


def _deadline_in_text(raw_instruction: str, value: float) -> bool:
    """Match a deadline value tolerating 10 vs 10.0 vs 10.00 formatting."""
    raw = raw_instruction or ""
    if float(value).is_integer():
        return re.search(rf"(?<!\d){int(value)}(\.0+)?(?!\d)", raw) is not None
    trimmed = ("%f" % value).rstrip("0").rstrip(".")
    return re.search(rf"(?<!\d){re.escape(trimmed)}\d*(?!\d)", raw) is not None


def _canonical_relation(rtype: Optional[str]) -> str:
    rtype = (rtype or "sequence").strip()
    return RELATION_ALIASES.get(rtype, rtype)


def tier1_check(plan: Optional[dict[str, Any]], instruction: Optional[str]) -> list[Discrepancy]:
    """High-precision deterministic NL<->plan cross-check.

    Only checks the semantic layer (actor/action/target/relation/condition/time_window).
    All checks are plan->NL ("the plan encodes X; does the instruction mention X?"), which
    structurally avoids the resource/capability false-positive class (NL prose like
    "ammunition for two strikes" is never inspected).
    """
    out: list[Discrepancy] = []
    if not plan or not instruction:
        return out

    norm = _normalize(instruction)
    tasks = plan.get("tasks", []) or []
    relations = plan.get("relations", []) or []

    # --- entity presence (soft): target / actor / action verbalized per task ---
    for task in tasks:
        tid = task.get("task_id", "?")
        target = task.get("target")
        if target and not _contains(norm, str(target)):
            out.append(Discrepancy(
                "target_absent", "soft", tid,
                nl_implies="(target not found in instruction)", plan_has=str(target),
                detail=f"plan target {target!r} for {tid} not mentioned in instruction",
                tier="lexical",
            ))
        actor = task.get("actor")
        if actor and not _contains(norm, str(actor)):
            out.append(Discrepancy(
                "actor_absent", "soft", tid,
                nl_implies="(actor not found in instruction)", plan_has=str(actor),
                detail=f"plan actor {actor!r} for {tid} not mentioned in instruction",
                tier="lexical",
            ))
        action = task.get("action")
        syns = _ACTION_SYNONYMS.get(str(action), (str(action),))
        if action and not any(_contains(norm, s) for s in syns):
            out.append(Discrepancy(
                "action_absent", "soft", tid,
                nl_implies="(action verb not found in instruction)", plan_has=str(action),
                detail=f"plan action {action!r} for {tid} (or a synonym) not mentioned",
                tier="lexical",
            ))

    # --- deadline presence (soft) ---
    for task in tasks:
        tw = task.get("time_window") or {}
        dl = tw.get("deadline")
        if dl is not None and not _deadline_in_text(instruction, float(dl)):
            out.append(Discrepancy(
                "deadline_absent", "soft", task.get("task_id", "?"),
                nl_implies="(no matching deadline value)", plan_has=f"deadline={dl}",
                detail=f"plan deadline {dl} for {task.get('task_id')} not found in instruction",
                tier="lexical",
            ))

    # --- relation-family conflict (strong): NL implies a relation family the plan lacks ---
    if len(tasks) > 1:
        rel_types = {_canonical_relation(r.get("type") or r.get("relation")) for r in relations}
        has_group_sync = any(
            (c.get("type") == "group_sync") for c in (plan.get("explicit_constraints") or [])
        )
        has_condition = (
            "condition_trigger" in rel_types
            or any(t.get("condition") for t in tasks)
            or any(r.get("condition") for r in relations)
        )

        def _satisfied(family: str) -> bool:
            if family == "sync":
                return "sync" in rel_types or has_group_sync
            if family == "condition_trigger":
                return "condition_trigger" in rel_types or has_condition
            if family == "sequence":
                # condition_trigger also encodes temporal precedence ("after X is
                # confirmed, do Y"), so an ordering keyword like then/after is satisfied
                # by it too -- avoids false positives on condition-gated sequences.
                return "sequence" in rel_types or "condition_trigger" in rel_types
            return family in rel_types

        for family, keywords in _RELATION_KEYWORDS.items():
            if family not in VALID_RELATION_TYPES:
                continue
            hit = next((kw for kw in keywords if _normalize(kw).strip() and
                        _normalize(kw).strip() in norm), None)
            if hit and not _satisfied(family):
                out.append(Discrepancy(
                    "relation_mismatch", "strong", "",
                    nl_implies=f"{family} (keyword {hit!r})",
                    plan_has=f"relations={sorted(rel_types)}"
                              + (" +group_sync" if has_group_sync else ""),
                    detail=f"instruction keyword {hit!r} implies a {family} relation, "
                           f"but the plan encodes none",
                    tier="lexical",
                ))

    return out


# --------------------------------------------------------------------------- #
# Component 3: tier2_check (LLM semantic judge; opt-in, advisory)
# --------------------------------------------------------------------------- #
_TIER2_SYSTEM = (
    "You are a strict semantic-consistency judge for a drone-swarm task-planning dataset. "
    "You are given a natural-language instruction and a structured task plan derived from it. "
    "Decide whether the plan faithfully encodes the instruction's task semantics."
)

_TIER2_RULES = (
    "Rules:\n"
    "1. Only judge the SEMANTIC layer: actor, action, target, relation type "
    "(sequence/parallel/sync/condition_trigger/group_sync/...), condition, and time_window "
    "(earliest/latest/deadline).\n"
    "2. IGNORE any resource/capability statements in the instruction (e.g. 'carries enough "
    "ammunition for two strikes', energy, capabilities). Those map to external config, NOT "
    "to plan fields; their absence from the plan is CORRECT, never a discrepancy.\n"
    "3. Do NOT invent system parameters (duration, energy_cost, ammo_cost). They are not in "
    "scope.\n"
    "4. A group synchronization in the instruction may be encoded as an explicit group_sync "
    "constraint rather than a 'sync' relation; treat that as consistent.\n"
    f"Valid relation types: {sorted(VALID_RELATION_TYPES)}.\n"
    "Respond with ONLY a JSON object:\n"
    '{"consistent": true|false, "discrepancies": [{"kind": "...", "locus": "...", '
    '"nl_implies": "...", "plan_has": "...", "detail": "..."}]}'
)


def build_tier2_messages(plan: dict[str, Any], instruction: str) -> list[dict[str, str]]:
    user = (
        f"{_TIER2_RULES}\n\n"
        f"INSTRUCTION:\n{instruction}\n\n"
        f"PLAN (verbalized):\n{verbalize(plan)}\n\n"
        f"PLAN (json):\n{json.dumps(plan, ensure_ascii=False, sort_keys=True)}"
    )
    return [
        {"role": "system", "content": _TIER2_SYSTEM},
        {"role": "user", "content": user},
    ]


def tier2_check(
    plan: Optional[dict[str, Any]],
    instruction: Optional[str],
    client: Any,
) -> Optional[list[Discrepancy]]:
    """Ask an LLM to judge semantic consistency. Returns discrepancies, or ``None`` if the
    LLM is unavailable / the response is unparsable (advisory: never raises, never blocks).

    ``client`` must expose ``generate(messages) -> response`` with a ``.text`` attribute
    (i.e. ``agents.llm_client.LLMClient``). Kept as a duck-typed parameter so this module
    imports cleanly without an API key and is trivial to stub in tests.
    """
    if not plan or not instruction or client is None:
        return None

    # Local import keeps the engine importable without the agents package / API deps.
    try:
        from agents.json_extraction import extract_json_object
    except Exception:
        return None

    try:
        response = client.generate(build_tier2_messages(plan, instruction))
    except Exception:
        return None

    text = getattr(response, "text", "") or ""
    return parse_tier2_response(text)


def parse_tier2_response(text: str) -> Optional[list[Discrepancy]]:
    """Map a judge JSON response to Discrepancy objects. ``None`` on unparsable/empty."""
    try:
        from agents.json_extraction import extract_json_object
    except Exception:
        return None

    extraction = extract_json_object(text or "")
    if not getattr(extraction, "ok", False) or not isinstance(extraction.data, dict):
        return None

    data = extraction.data
    items = data.get("discrepancies") or []
    if data.get("consistent") is True and not items:
        return []

    out: list[Discrepancy] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        out.append(Discrepancy(
            kind=str(item.get("kind", "semantic_mismatch")),
            severity="strong",
            locus=str(item.get("locus", "")),
            nl_implies=str(item.get("nl_implies", "")),
            plan_has=str(item.get("plan_has", "")),
            detail=str(item.get("detail", "")),
            tier="llm",
        ))
    return out


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def check_case(
    case: dict[str, Any],
    *,
    run_llm: bool = False,
    client: Any = None,
) -> CaseReport:
    """Run the requested tiers over a single master case and collect discrepancies."""
    sample_id = str(case.get("sample_id", "<missing>"))
    plan = case.get("canonical_task_plan")
    instruction = case.get("standard_instruction") or case.get("raw_instruction")

    report = CaseReport(sample_id=sample_id, consistent=True, verbalized=verbalize(plan))
    if not plan or not instruction:
        # Nothing to compare (e.g. un-backfilled raw case). Not a drift; leave consistent.
        return report

    report.discrepancies.extend(tier1_check(plan, instruction))

    if run_llm:
        llm_result = tier2_check(plan, instruction, client)
        if llm_result is None:
            report.llm_skipped = True
        else:
            report.discrepancies.extend(llm_result)

    report.consistent = not report.discrepancies
    return report
