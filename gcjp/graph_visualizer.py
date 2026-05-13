"""
gcjp/graph_visualizer.py
将 BuiltGraph 渲染为静态图：节点按 actor 上色，边按 relation 区分线型，
右侧侧栏列出全部约束（source_label / applies_to / 关键参数）。

设计目标：
    - 复用现有 networkx 依赖，不引入新包；
    - 默认拓扑分层布局，让 DAG 走向清晰；
    - 输出可写入 PNG / SVG / PDF；
    - 中文 task_id / segment_id 可正常渲染（依赖系统 CJK 字体）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import networkx as nx
from matplotlib.figure import Figure
from matplotlib.lines import Line2D

from gcjp.mission_graph import BuiltGraph, Constraint


# ─────────────────────────────────────────────────────────────────────────────
# 字体：保证中文标签可渲染
# ─────────────────────────────────────────────────────────────────────────────

_CJK_FONT_CANDIDATES = [
    "Microsoft YaHei", "SimHei",
    "WenQuanYi Zen Hei", "Heiti SC", "PingFang SC",
    "DejaVu Sans",
]


def _ensure_font() -> None:
    plt.rcParams["font.sans-serif"] = _CJK_FONT_CANDIDATES
    plt.rcParams["axes.unicode_minus"] = False


# ─────────────────────────────────────────────────────────────────────────────
# 布局：拓扑分层
# ─────────────────────────────────────────────────────────────────────────────

def _topological_layout(g: nx.DiGraph) -> dict[str, tuple[float, float]]:
    """按拓扑深度分层，同层节点垂直排列。若图含环则退回 spring_layout。"""
    if not nx.is_directed_acyclic_graph(g):
        return nx.spring_layout(g, seed=42)

    level: dict[str, int] = {}
    for node in nx.topological_sort(g):
        preds = list(g.predecessors(node))
        level[node] = max((level[p] for p in preds), default=-1) + 1

    by_level: dict[int, list[str]] = {}
    for node, lvl in level.items():
        by_level.setdefault(lvl, []).append(node)

    pos: dict[str, tuple[float, float]] = {}
    for lvl, nodes in by_level.items():
        nodes_sorted = sorted(nodes)
        n = len(nodes_sorted)
        for i, node in enumerate(nodes_sorted):
            pos[node] = (float(lvl), float((n - 1) / 2 - i))
    return pos


# ─────────────────────────────────────────────────────────────────────────────
# 颜色 / 线型
# ─────────────────────────────────────────────────────────────────────────────

_ACTOR_PALETTE = list(plt.get_cmap("tab10").colors)


def _actor_color_map(actors: Iterable[str]) -> dict[str, tuple]:
    actors_list = sorted(set(actors))
    return {a: _ACTOR_PALETTE[i % len(_ACTOR_PALETTE)] for i, a in enumerate(actors_list)}


_RELATION_STYLES: dict[str, dict] = {
    "sequence":          {"style": "solid",  "color": "#222222", "width": 1.4},
    "condition_trigger": {"style": "solid",  "color": "#1f77b4", "width": 1.4},
    "sync":              {"style": "dashed", "color": "#ff7f0e", "width": 1.6},
    "barrier":           {"style": "solid",  "color": "#2ca02c", "width": 1.4},
    "join":              {"style": "solid",  "color": "#2ca02c", "width": 1.4},
    "handoff":           {"style": "solid",  "color": "#2ca02c", "width": 1.4},
    "fork":              {"style": "dotted", "color": "#9467bd", "width": 1.4},
    "parallel":          {"style": "dashed", "color": "#888888", "width": 1.0},
}
_DEFAULT_RELATION_STYLE = {"style": "solid", "color": "#222222", "width": 1.4}


def _style_for_relation(rel: str) -> dict:
    return _RELATION_STYLES.get(rel, _DEFAULT_RELATION_STYLE)


def _wrap_task_id(task_id: str, soft_limit: int = 14) -> str:
    """长 task_id 按 `_` 折行，避免溢出节点。短 id 原样返回。"""
    if len(task_id) <= soft_limit:
        return task_id
    parts = task_id.split("_")
    if len(parts) <= 1:
        return task_id
    lines: list[str] = []
    current = parts[0]
    for p in parts[1:]:
        if len(current) + 1 + len(p) <= soft_limit:
            current = f"{current}_{p}"
        else:
            lines.append(current)
            current = p
    lines.append(current)
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# 约束侧栏文案
# ─────────────────────────────────────────────────────────────────────────────

_INTEREST_KEYS = (
    "task_id", "before", "after", "task_i", "task_j", "actor",
    "earliest", "latest", "deadline", "tolerance",
    "max_value", "resource_type", "min_duration_units",
)


def _format_constraint_line(c: Constraint) -> str:
    """单条约束 → 1-2 行字符串。"""
    head = f"[{c.constraint_type}] {c.source_label}"
    parts: list[str] = []
    for key in _INTEREST_KEYS:
        if key in c.params and c.params[key] is not None:
            val = c.params[key]
            if isinstance(val, float):
                parts.append(f"{key}={val:g}")
            else:
                parts.append(f"{key}={val}")
    if "required" in c.params:
        parts.append(f"required={c.params['required']}")
    body = ", ".join(parts)
    return f"{head}\n  {body}" if body else head


# ─────────────────────────────────────────────────────────────────────────────
# Hover tooltip 格式化（matplotlib 纯文本 / pyvis HTML 两套）
# ─────────────────────────────────────────────────────────────────────────────

def _format_node_tooltip_text(graph: BuiltGraph, task_id: str) -> str:
    """matplotlib hover 用纯文本 tooltip。"""
    node = graph.nodes[task_id]
    dur_ub = f"{node.duration_ub:g}" if node.duration_ub is not None else "—"
    lines = [
        f"task_id: {task_id}",
        f"actor:   {node.actor}",
        f"action:  {node.action}",
        f"target:  {node.target}",
        f"duration: {node.duration_lb:g} ~ {dur_ub}",
        "─" * 24,
        f"required_capability: {node.required_capability}",
        f"energy_cost: {node.energy_cost} kWh",
        f"ammo_cost:   {node.ammo_cost}",
    ]
    if (node.time_window_earliest is not None
            or node.time_window_latest is not None):
        lines.append(
            f"time_window: earliest={node.time_window_earliest}, "
            f"latest={node.time_window_latest}"
        )
    return "\n".join(lines)


def _format_node_tooltip_html(graph: BuiltGraph, task_id: str) -> str:
    """pyvis hover 用 HTML 富文本 tooltip。"""
    node = graph.nodes[task_id]
    dur_ub = f"{node.duration_ub:g}" if node.duration_ub is not None else "—"
    rows = [
        f"<b>{task_id}</b>",
        "<table style='font-size:12px;border-collapse:collapse'>",
        f"<tr><td><b>actor:</b></td><td>{node.actor}</td></tr>",
        f"<tr><td><b>action:</b></td><td>{node.action}</td></tr>",
        f"<tr><td><b>target:</b></td><td>{node.target}</td></tr>",
        f"<tr><td><b>duration:</b></td><td>{node.duration_lb:g} ~ {dur_ub}</td></tr>",
        f"<tr><td><b>capability:</b></td><td>{node.required_capability}</td></tr>",
        f"<tr><td><b>energy:</b></td><td>{node.energy_cost} kWh</td></tr>",
        f"<tr><td><b>ammo:</b></td><td>{node.ammo_cost}</td></tr>",
    ]
    if (node.time_window_earliest is not None
            or node.time_window_latest is not None):
        rows.append(
            f"<tr><td><b>time_window:</b></td>"
            f"<td>earliest={node.time_window_earliest}, "
            f"latest={node.time_window_latest}</td></tr>"
        )
    rows.append("</table>")
    return "".join(rows)


def _format_edge_tooltip_text(u: str, v: str, data: dict) -> str:
    """matplotlib hover 用纯文本 tooltip（边）。"""
    rel = data.get("relation", "sequence")
    lines = [f"{u} → {v}", f"relation: {rel}"]
    if data.get("sync_tolerance") is not None:
        lines.append(f"sync_tolerance: {data['sync_tolerance']}")
    if data.get("condition"):
        lines.append(f"condition: {data['condition']}")
    return "\n".join(lines)


def _format_edge_tooltip_html(u: str, v: str, data: dict) -> str:
    """pyvis hover 用 HTML tooltip（边）。"""
    rel = data.get("relation", "sequence")
    rows = [
        f"<b>{u} → {v}</b>",
        "<table style='font-size:12px;border-collapse:collapse'>",
        f"<tr><td><b>relation:</b></td><td>{rel}</td></tr>",
    ]
    if data.get("sync_tolerance") is not None:
        rows.append(f"<tr><td><b>sync_tolerance:</b></td>"
                    f"<td>{data['sync_tolerance']}</td></tr>")
    if data.get("condition"):
        rows.append(f"<tr><td><b>condition:</b></td>"
                    f"<td>{data['condition']}</td></tr>")
    rows.append("</table>")
    return "".join(rows)


def _format_constraints_panel(constraints: list[Constraint],
                              max_lines: int = 80) -> str:
    if not constraints:
        return "(no constraints)"

    grouped: dict[str, list[Constraint]] = {}
    for c in constraints:
        grouped.setdefault(c.constraint_type, []).append(c)

    lines: list[str] = []
    for ctype in sorted(grouped):
        lines.append(f"━ {ctype} ━")
        for c in grouped[ctype]:
            lines.append(_format_constraint_line(c))
        lines.append("")
    text = "\n".join(lines).rstrip()

    text_lines = text.splitlines()
    if len(text_lines) > max_lines:
        text_lines = text_lines[:max_lines]
        text_lines.append(f"... (truncated, {len(constraints)} 条约束已部分省略)")
        text = "\n".join(text_lines)
    return text


# ─────────────────────────────────────────────────────────────────────────────
# 主入口
# ─────────────────────────────────────────────────────────────────────────────

def _auto_figsize(graph: BuiltGraph, has_panel: bool) -> tuple[float, float]:
    """根据节点数 / 拓扑深度 / 约束数估算合适画布尺寸。"""
    g = graph.graph
    n_nodes = max(1, g.number_of_nodes())
    if nx.is_directed_acyclic_graph(g) and n_nodes > 0:
        depth = {}
        for node in nx.topological_sort(g):
            preds = list(g.predecessors(node))
            depth[node] = max((depth[p] for p in preds), default=-1) + 1
        max_depth = max(depth.values()) + 1 if depth else 1
        max_width = max(
            sum(1 for d in depth.values() if d == lvl)
            for lvl in range(max_depth)
        )
    else:
        max_depth = max(1, int(n_nodes ** 0.5))
        max_width = max(1, int(n_nodes ** 0.5))

    main_w = max(6.5, min(14, 2.6 * max_depth + 2.5))
    main_h = max(4.0, min(10, 1.4 * max_width + 2.0))
    panel_w = main_w * 0.48 if has_panel else 0
    panel_h_need = 1.0 + 0.18 * len(graph.constraints)
    total_h = max(main_h, min(12, panel_h_need + 1.5))
    return (main_w + panel_w, total_h)


def visualize_built_graph(
    graph: BuiltGraph,
    output_path: str | Path | None = None,
    *,
    show: bool = False,
    figsize: tuple[float, float] | None = None,
    layout: str = "topological",
    show_constraints_panel: bool = True,
    title: str | None = None,
) -> Figure:
    """
    渲染 BuiltGraph 为静态图。

    Args:
        graph:                  BuiltGraph 实例
        output_path:            若给定则保存到文件，后缀决定格式（png/svg/pdf）
        show:                   是否调 plt.show()（脚本调用建议保持 False）
        figsize:                画布尺寸（英寸）
        layout:                 'topological' / 'spring' / 'kamada_kawai' / 'shell'
        show_constraints_panel: 是否在右侧显示约束清单
        title:                  图标题（默认: f"BuiltGraph: {segment_id}"）

    Returns:
        matplotlib.figure.Figure
    """
    _ensure_font()
    g = graph.graph

    # 1. 布局
    if layout == "topological":
        pos = _topological_layout(g)
    elif layout == "spring":
        pos = nx.spring_layout(g, seed=42)
    elif layout == "kamada_kawai":
        pos = nx.kamada_kawai_layout(g)
    elif layout == "shell":
        pos = nx.shell_layout(g)
    else:
        raise ValueError(f"unknown layout: {layout!r}")

    # 2. 颜色映射
    actor_color = _actor_color_map(graph.actor_set)

    # 3. 画布：单轴 vs 主轴+侧栏（figsize 未给定时自适应）
    if figsize is None:
        figsize = _auto_figsize(graph, has_panel=show_constraints_panel)
    if show_constraints_panel:
        fig, (ax, ax_panel) = plt.subplots(
            1, 2, figsize=figsize,
            gridspec_kw={"width_ratios": [3, 1.3]},
        )
    else:
        fig, ax = plt.subplots(figsize=figsize)
        ax_panel = None

    # 4. 节点（用显式 nodelist 锁定顺序，便于 mplcursors 按 index 反查）
    nodelist = list(g.nodes)
    node_colors = [actor_color[graph.nodes[n].actor] for n in nodelist]
    node_collection = nx.draw_networkx_nodes(
        g, pos,
        nodelist=nodelist,
        node_color=node_colors,
        node_size=2400,
        edgecolors="black",
        linewidths=1.2,
        ax=ax,
    )

    # 节点标签：task_id（按 _ 折行避免溢出）+ [action] + duration
    labels = {
        n: f"{_wrap_task_id(n)}\n[{graph.nodes[n].action}]\nd={graph.nodes[n].duration_lb:g}"
        for n in g.nodes
    }
    nx.draw_networkx_labels(g, pos, labels=labels, font_size=7, ax=ax)

    # 5. 边（按 relation 分组绘制）
    edges_by_relation: dict[str, list[tuple[str, str]]] = {}
    for u, v, data in g.edges(data=True):
        rel = data.get("relation", "sequence")
        edges_by_relation.setdefault(rel, []).append((u, v))

    for rel, edge_list in edges_by_relation.items():
        style = _style_for_relation(rel)
        nx.draw_networkx_edges(
            g, pos,
            edgelist=edge_list,
            edge_color=style["color"],
            style=style["style"],
            width=style["width"],
            arrows=True,
            arrowsize=18,
            arrowstyle="->",
            node_size=2400,  # 与节点大小一致，避免箭头被遮挡
            ax=ax,
        )

    # 6. 边标签（sync / condition_trigger / 其他非 sequence 关系）
    edge_labels: dict[tuple[str, str], str] = {}
    for u, v, data in g.edges(data=True):
        rel = data.get("relation", "sequence")
        if rel == "sync" and data.get("sync_tolerance") is not None:
            edge_labels[(u, v)] = f"sync±{data['sync_tolerance']:g}"
        elif rel == "condition_trigger" and data.get("condition"):
            cond = str(data["condition"])
            if len(cond) > 24:
                cond = cond[:21] + "..."
            edge_labels[(u, v)] = cond
        elif rel not in ("sequence", "sync", "condition_trigger"):
            edge_labels[(u, v)] = rel
    if edge_labels:
        nx.draw_networkx_edge_labels(
            g, pos,
            edge_labels=edge_labels,
            font_size=7,
            ax=ax,
            bbox={"boxstyle": "round,pad=0.1", "fc": "white", "ec": "none", "alpha": 0.7},
        )

    ax.set_title(title or f"BuiltGraph: {graph.segment_id}", fontsize=12)
    ax.axis("off")

    # 收紧主轴边界，避免大块空白
    if pos:
        xs = [p[0] for p in pos.values()]
        ys = [p[1] for p in pos.values()]
        pad_x = max(0.6, 0.15 * (max(xs) - min(xs) + 1))
        pad_y = max(0.6, 0.25 * (max(ys) - min(ys) + 1))
        ax.set_xlim(min(xs) - pad_x, max(xs) + pad_x)
        ax.set_ylim(min(ys) - pad_y, max(ys) + pad_y)

    # 7. 图例（actor + relation）
    actor_patches = [
        mpatches.Patch(color=actor_color[a], label=a)
        for a in sorted(actor_color)
    ]
    relation_handles = [
        Line2D(
            [0], [0],
            color=_style_for_relation(rel)["color"],
            linestyle=_style_for_relation(rel)["style"],
            linewidth=_style_for_relation(rel)["width"],
            label=rel,
        )
        for rel in sorted(edges_by_relation)
    ]
    ax.legend(
        handles=actor_patches + relation_handles,
        loc="lower left",
        fontsize=8,
        title="actor / relation",
        title_fontsize=9,
        frameon=True,
    )

    # 8. 侧栏：约束清单
    if ax_panel is not None:
        ax_panel.axis("off")
        ax_panel.set_title(f"Constraints ({len(graph.constraints)})",
                           fontsize=11, loc="left")
        panel_text = _format_constraints_panel(graph.constraints)
        ax_panel.text(
            0.0, 1.0, panel_text,
            transform=ax_panel.transAxes,
            fontsize=7.5,
            verticalalignment="top",
            family="monospace",
        )

    fig.tight_layout()

    if output_path is not None:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(out, dpi=160, bbox_inches="tight")

    # show=True 时尝试挂 mplcursors hover tooltip（节点）
    if show:
        _try_register_hover(node_collection, nodelist, graph)
        plt.show()

    return fig


# ─────────────────────────────────────────────────────────────────────────────
# pyvis 交互式 HTML 输出
# ─────────────────────────────────────────────────────────────────────────────

_PYVIS_HIERARCHICAL_OPTIONS = """
{
  "layout": {
    "hierarchical": {
      "enabled": true,
      "direction": "LR",
      "sortMethod": "directed",
      "levelSeparation": 220,
      "nodeSpacing": 130,
      "treeSpacing": 200
    }
  },
  "physics": { "enabled": false },
  "edges": {
    "smooth": {
      "enabled": true,
      "type": "cubicBezier",
      "forceDirection": "horizontal",
      "roundness": 0.35
    },
    "arrows": { "to": { "enabled": true, "scaleFactor": 0.9 } }
  },
  "interaction": {
    "hover": true,
    "tooltipDelay": 100,
    "navigationButtons": true,
    "keyboard": true
  }
}
"""


def _rgb_to_hex(rgb: tuple) -> str:
    r, g, b = rgb[:3]
    return f"#{int(r * 255):02x}{int(g * 255):02x}{int(b * 255):02x}"


def _edge_short_label(rel: str, data: dict) -> str:
    """边主体上的短标签（与静态版保持一致）。"""
    if rel == "sync" and data.get("sync_tolerance") is not None:
        return f"sync±{data['sync_tolerance']:g}"
    if rel == "condition_trigger" and data.get("condition"):
        cond = str(data["condition"])
        return cond if len(cond) <= 24 else cond[:21] + "..."
    if rel != "sequence":
        return rel
    return ""


def visualize_built_graph_html(
    graph: BuiltGraph,
    output_path: str | Path,
    *,
    open_in_browser: bool = False,
    height: str = "800px",
    width: str = "100%",
) -> Path:
    """
    用 pyvis 输出可交互的 HTML 视图：
        - 节点 / 边均可拖拽、缩放
        - hover 节点显示 task_id / actor / action / target / duration /
          required_capability / energy / ammo / time_window
        - hover 边显示 relation / sync_tolerance / condition
        - 左右分层布局（LR），适合 DAG

    Args:
        graph:           BuiltGraph 实例
        output_path:     输出 .html 路径
        open_in_browser: 是否完成后自动用默认浏览器打开
        height/width:   嵌入页面的尺寸（CSS 字符串）

    Returns:
        实际写出的 Path 对象。

    Raises:
        ImportError: 若 pyvis 未安装。
    """
    try:
        from pyvis.network import Network
    except ImportError as exc:
        raise ImportError(
            "visualize_built_graph_html 需要 pyvis；请先 `pip install pyvis`。"
        ) from exc

    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    g = graph.graph
    actor_color = _actor_color_map(graph.actor_set)
    actor_color_hex = {a: _rgb_to_hex(c) for a, c in actor_color.items()}

    net = Network(
        directed=True,
        height=height,
        width=width,
        bgcolor="#ffffff",
        font_color="#222222",
        cdn_resources="in_line",  # 自包含 HTML，无需联网
    )
    net.set_options(_PYVIS_HIERARCHICAL_OPTIONS)

    # 节点
    for task_id in g.nodes:
        node = graph.nodes[task_id]
        net.add_node(
            task_id,
            label=_wrap_task_id(task_id),
            title=_format_node_tooltip_html(graph, task_id),
            color=actor_color_hex[node.actor],
            shape="ellipse",
            borderWidth=1.5,
        )

    # 边
    for u, v, data in g.edges(data=True):
        rel = data.get("relation", "sequence")
        style = _style_for_relation(rel)
        dashed = style["style"] in ("dashed", "dotted")
        net.add_edge(
            u, v,
            title=_format_edge_tooltip_html(u, v, data),
            color=style["color"],
            dashes=dashed,
            width=max(1.5, style["width"]),
            label=_edge_short_label(rel, data),
            font={"size": 11, "color": style["color"], "align": "middle"},
        )

    # pyvis 0.3.x: write_html(name, notebook=False, open_browser=...)
    try:
        net.write_html(str(out_path), notebook=False, open_browser=open_in_browser)
    except TypeError:
        net.write_html(str(out_path), notebook=False)
        if open_in_browser:
            import webbrowser
            webbrowser.open(f"file://{out_path.resolve()}")

    return out_path


def _try_register_hover(node_collection, nodelist: list[str],
                        graph: BuiltGraph) -> None:
    """若 mplcursors 可用，则为节点注册 hover tooltip；否则静默跳过。"""
    try:
        import mplcursors
    except ImportError:
        return

    cursor = mplcursors.cursor(node_collection, hover=True)

    @cursor.connect("add")
    def _on_add(sel):  # noqa: ANN001 — mplcursors Selection
        try:
            idx = int(sel.index)
        except (TypeError, ValueError):
            return
        if 0 <= idx < len(nodelist):
            sel.annotation.set_text(
                _format_node_tooltip_text(graph, nodelist[idx])
            )
            sel.annotation.get_bbox_patch().set(fc="lightyellow", alpha=0.95)


# ─────────────────────────────────────────────────────────────────────────────
# 离线可视化：从 JSON 文件加载并渲染 pyvis HTML
# ─────────────────────────────────────────────────────────────────────────────

def visualize_from_file(
    json_path: str | Path,
    output_html_path: str | Path | None = None,
    *,
    open_in_browser: bool = True,
    height: str = "900px",
    width: str = "100%",
) -> Path:
    """
    从保存的 JSON 加载 BuiltGraph 并渲染 pyvis 交互式 HTML。

    Args:
        json_path:        BuiltGraph.save() 产出的 .json 文件路径
        output_html_path: 输出 HTML 路径（默认与 json 同目录同名 .html）
        open_in_browser:  完成后自动用默认浏览器打开
        height/width:     嵌入页面的尺寸

    Returns:
        生成的 HTML 文件 Path。
    """
    json_path = Path(json_path)
    graph = BuiltGraph.load(json_path)
    if output_html_path is None:
        output_html_path = json_path.with_suffix(".html")
    return visualize_built_graph_html(
        graph, output_html_path,
        open_in_browser=open_in_browser, height=height, width=width,
    )
