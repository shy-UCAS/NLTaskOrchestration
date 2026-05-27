"""
BuiltGraph 对比评估器：reference vs prediction 的节点/边/约束 F1。

节点匹配用 (actor, action, target) 三元组而非 task_id，
因为 LLM 生成的 task_id 通常与 reference 不同。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from gcjp.mission_graph import BuiltGraph, TaskNode, DependencyEdge, Constraint


@dataclass
class GraphComparisonResult:
    node_set_f1: float
    node_precision: float
    node_recall: float
    edge_set_f1: float
    edge_precision: float
    edge_recall: float
    constraint_f1: float
    constraint_precision: float
    constraint_recall: float
    attribute_match: dict[str, Any] = field(default_factory=dict)
    details: dict[str, Any] = field(default_factory=dict)


def compare_graphs(
    reference: BuiltGraph,
    prediction: BuiltGraph,
) -> GraphComparisonResult:
    node_result, node_mapping = _compare_nodes(reference, prediction)
    edge_result = _compare_edges(reference, prediction, node_mapping)
    constraint_result = _compare_constraints(reference, prediction, node_mapping)
    attr_match = _compare_attributes(reference, prediction, node_mapping)

    return GraphComparisonResult(
        node_set_f1=node_result["f1"],
        node_precision=node_result["precision"],
        node_recall=node_result["recall"],
        edge_set_f1=edge_result["f1"],
        edge_precision=edge_result["precision"],
        edge_recall=edge_result["recall"],
        constraint_f1=constraint_result["f1"],
        constraint_precision=constraint_result["precision"],
        constraint_recall=constraint_result["recall"],
        attribute_match=attr_match,
        details={
            "node_details": node_result,
            "edge_details": edge_result,
            "constraint_details": constraint_result,
        },
    )


NodeTriple = tuple[str, str, str]


def _node_triple(node: TaskNode) -> NodeTriple:
    return (node.actor, node.action, node.target)


def _compare_nodes(
    ref: BuiltGraph, pred: BuiltGraph,
) -> tuple[dict[str, Any], dict[str, str]]:
    """比较节点集合，返回 F1 指标和 pred→ref 的 task_id 映射。"""
    ref_triples: dict[NodeTriple, str] = {}
    for tid, node in ref.nodes.items():
        triple = _node_triple(node)
        ref_triples[triple] = tid

    pred_triples: dict[NodeTriple, str] = {}
    for tid, node in pred.nodes.items():
        triple = _node_triple(node)
        pred_triples[triple] = tid

    ref_set = set(ref_triples.keys())
    pred_set = set(pred_triples.keys())

    tp = ref_set & pred_set
    precision = len(tp) / len(pred_set) if pred_set else 1.0
    recall = len(tp) / len(ref_set) if ref_set else 1.0
    f1 = _f1(precision, recall)

    mapping: dict[str, str] = {}
    for triple in tp:
        mapping[pred_triples[triple]] = ref_triples[triple]

    return {
        "f1": f1,
        "precision": precision,
        "recall": recall,
        "ref_count": len(ref_set),
        "pred_count": len(pred_set),
        "matched": len(tp),
        "ref_only": sorted(ref_set - pred_set),
        "pred_only": sorted(pred_set - tp),
    }, mapping


def _compare_edges(
    ref: BuiltGraph,
    pred: BuiltGraph,
    node_mapping: dict[str, str],
) -> dict[str, Any]:
    """比较边集合，通过 node_mapping 将 pred 的 task_id 映射到 ref 空间。"""

    def edge_key(edge: DependencyEdge, mapping: dict[str, str] | None = None) -> tuple:
        src = mapping.get(edge.source, edge.source) if mapping else edge.source
        tgt = mapping.get(edge.target, edge.target) if mapping else edge.target
        return (src, tgt, edge.relation)

    ref_edges = {edge_key(e) for e in ref.edges}
    pred_edges = {edge_key(e, node_mapping) for e in pred.edges}

    tp = ref_edges & pred_edges
    precision = len(tp) / len(pred_edges) if pred_edges else 1.0
    recall = len(tp) / len(ref_edges) if ref_edges else 1.0

    return {
        "f1": _f1(precision, recall),
        "precision": precision,
        "recall": recall,
        "ref_count": len(ref_edges),
        "pred_count": len(pred_edges),
        "matched": len(tp),
    }


def _compare_constraints(
    ref: BuiltGraph,
    pred: BuiltGraph,
    node_mapping: dict[str, str],
) -> dict[str, Any]:
    """比较约束集合，用 (constraint_type, frozenset(mapped_applies_to)) 做匹配。"""

    def constraint_key(
        c: Constraint, mapping: dict[str, str] | None = None,
    ) -> tuple:
        applies = [
            mapping.get(tid, tid) if mapping else tid
            for tid in (c.applies_to or [])
        ]
        return (c.constraint_type, frozenset(applies))

    ref_constraints = {constraint_key(c) for c in ref.constraints}
    pred_constraints = {constraint_key(c, node_mapping) for c in pred.constraints}

    tp = ref_constraints & pred_constraints
    precision = len(tp) / len(pred_constraints) if pred_constraints else 1.0
    recall = len(tp) / len(ref_constraints) if ref_constraints else 1.0

    return {
        "f1": _f1(precision, recall),
        "precision": precision,
        "recall": recall,
        "ref_count": len(ref_constraints),
        "pred_count": len(pred_constraints),
        "matched": len(tp),
    }


def _compare_attributes(
    ref: BuiltGraph,
    pred: BuiltGraph,
    node_mapping: dict[str, str],
) -> dict[str, Any]:
    """对已匹配节点做逐属性精确比对。"""
    attr_keys = ("duration_lb", "energy_cost", "ammo_cost")
    total_attrs = 0
    matched_attrs = 0
    per_attr: dict[str, dict[str, int]] = {k: {"total": 0, "matched": 0} for k in attr_keys}

    pred_to_ref = node_mapping
    for pred_tid, ref_tid in pred_to_ref.items():
        pred_node = pred.nodes.get(pred_tid)
        ref_node = ref.nodes.get(ref_tid)
        if not pred_node or not ref_node:
            continue
        for attr in attr_keys:
            ref_val = getattr(ref_node, attr, None)
            pred_val = getattr(pred_node, attr, None)
            total_attrs += 1
            per_attr[attr]["total"] += 1
            if ref_val == pred_val:
                matched_attrs += 1
                per_attr[attr]["matched"] += 1

    return {
        "overall_accuracy": matched_attrs / total_attrs if total_attrs else 1.0,
        "total_attributes": total_attrs,
        "matched_attributes": matched_attrs,
        "per_attribute": per_attr,
    }


def _f1(precision: float, recall: float) -> float:
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)
