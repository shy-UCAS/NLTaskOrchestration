# NLTaskOrchestration

> Natural-language-driven and verifiable task graph construction for UAV swarm missions.

本项目面向无人机集群任务规划场景，研究如何将自然语言作战指令转化为结构化任务图，并通过图结构检查与 SMT/Z3 约束验证提升任务编排结果的可靠性。

当前阶段已完成 **Layer 1：确定性底座** 的核心建设。已实现从标准化任务计划 JSON → 任务图构建 → 多维度约束编码 → SAT/UNSAT 检测 → 验证报告输出的完整闭环，并接入动作模板、能力模型和环境配置三个外部配置文件。

---

## 1. 当前数据流

```text
自然语言作战指令
    ↓
标准化任务计划 JSON
    ↓
task_plan_schema.json 格式校验
    ↓
task_plan_loader.py  ─────── 读取 action_templates.yaml
    ↓                              capability_model.yaml
TaskGraphBuilder                    environment_facilities.yaml
    ↓
BuiltGraph
    ↓
VerificationPipeline
    ↓
图结构验证 + Z3 约束验证 + 验证报告
```

当前已跑通的三条关键路径：

```text
demo_01: 侦察—待机—拦截 SAT 正例
demo_02: 弹药资源超限 UNSAT 反例（5次打击 > max_ammo=4）
demo_03: 设施地图 UTM 坐标场景 + 环境引用校验 SAT 正例
```

---

## 2. 当前工程结构

```text
NLTaskOrchestration/
│
├── README.md
│
├── configs/
│   ├── action_templates.yaml          # 动作模板库（9种动作、参数、资源消耗）
│   ├── capability_model.yaml          # 集群能力模型（异构能力、资源上限）
│   ├── environment_config.yaml        # 场景环境配置（含 scenario_demo、scenario_simple）
│   └── environment_facilities.yaml    # 设施地图 UTM 坐标转换场景
│
├── schemas/
│   └── task_plan_schema.json          # 标准化任务计划 JSON Schema
│
├── gcjp/
│   ├── __init__.py
│   ├── api_spec.py                   # GCJP v1 受限 API 统一规范
│   ├── mission_graph.py               # TaskGraphBuilder 与 BuiltGraph
│   ├── code_executor.py               # GCJP 代码字符串 → BuiltGraph 执行入口
│   ├── constraint_templates.py        # Z3 约束构建与求解
│   ├── safety_checker.py              # GCJP 代码白名单检查
│   ├── task_plan_loader.py            # 标准化 JSON → BuiltGraph
│   ├── environment_model.py           # 环境引用校验与坐标距离计算
│   └── debug_logger.py                # 全局可控调试日志器
│
├── verifier/
│   ├── __init__.py
│   └── pipeline.py                    # 四层递进验证管道
│
├── demos/
│   ├── demo_01_simple_task_plan.json  # SAT 正例：侦察—待机—拦截
│   ├── demo_01_build_graph_from_json.py
│   ├── demo_02_resource_unsat_task_plan.json  # UNSAT 反例：弹药超限
│   ├── demo_02_build_resource_unsat_from_json.py
│   ├── demo_03_facilities_task_plan.json      # SAT 正例：设施 UTM 场景
│   ├── demo_03_build_facilities_from_json.py
│   ├── demo_01_simple_solo.py         # 手写 GCJP SAT 正例
│   ├── demo_05_unsat_example.py       # 手写 GCJP UNSAT 反例
│   └── demo_06_fixed_gcjp_api.py      # GCJP v1 代码字符串端到端验证
│
├── tools/
│   ├── validate_task_plan.py          # JSON Schema 格式校验
│   ├── inspect_task_plan.py           # 任务计划内容检查
│   ├── validate_configs.py            # 配置文件完整性校验
│   └── convert_facilities_to_yaml.py  # facilities.json → YAML 坐标转换
│
└── research/
    └── 研究计划1_工程实现清单_v2.md
```

---

## 3. 核心文件说明

### 3.1 `configs/` — 配置文件层

三个 YAML 配置文件提供动作参数、集群能力和场景数据，是系统确定性底座的外部知识来源。

#### `action_templates.yaml` — 动作模板库

定义 9 种无人机动作的白名单及其参数约束：

| 动作 | 能力要求 | 最短耗时 | 能量消耗 | 弹药消耗 |
| --- | --- | --- | --- | --- |
| `reconnaissance` | recon_capable | 2.0 | 3.0 kWh | 0 |
| `strike` | strike_capable | 1.5 | 5.0 kWh | 1 |
| `breakthrough` | strike_capable | 2.0 | 8.0 kWh | 1 |
| `fly_to` | — | 动态计算 | 动态计算 | 0 |
| `rendezvous` | — | 0.5 | 1.0 kWh | 0 |
| `standby` | — | 0.5 | 0.5 kWh | 0 |
| `jam` | jamming_capable | 3.0 | 10.0 kWh | 0 |
| `intercept` | strike_capable | 1.0 | 6.0 kWh | 1 |
| `track` | recon_capable | 2.0 | 4.0 kWh | 0 |

由 `load_action_defaults_from_yaml()` 读取。

#### `capability_model.yaml` — 集群能力模型

定义 4 个异构集群的能力和资源上限：

| 集群 | 能力 | 弹药上限 | 能量上限 | 巡航速度 |
| --- | --- | --- | --- | --- |
| fleet_1 | recon, strike | 4 | 50 kWh | 80 km/h |
| fleet_2 | strike | 6 | 60 kWh | 100 km/h |
| fleet_3 | jam, recon | 1 | 80 kWh | 70 km/h |
| fleet_4 | recon, strike | 5 | 45 kWh | 130 km/h |

由 `load_capability_model_from_yaml()` 读取，用于资源约束和能力匹配验证。

#### `environment_config.yaml` / `environment_facilities.yaml` — 场景环境配置

定义场景中的初始位置、目标点、威胁区、禁飞区等空间信息。`environment_facilities.yaml` 包含从 UTM 坐标转换的真实设施地图数据（scenario_facilities_utm 场景，以 hq_1 为原点）。

---

### 3.2 `schemas/task_plan_schema.json`

定义标准化任务计划的 JSON 格式，是自然语言解析结果与任务图构建模块之间的接口规范。

约束内容：
- `scenario_id`：关联的环境场景 ID；
- `participants`：参与任务的无人机集群（actor_id、type、role、capability_ref）；
- `tasks`：任务节点（task_id、actor、action、target、condition、time_window、expected_output）；
- `relations`：任务边，支持 sequence、sync、condition_trigger、barrier、handoff 等 8 种关系类型；
- `ambiguities`：自然语言解析中的歧义项；
- `missing_fields`：无法确定、需人工确认的信息。

---

### 3.3 `gcjp/task_plan_loader.py`

将标准化任务计划 JSON 转换为 `TaskGraphBuilder` 调用，输出 `BuiltGraph`。

核心函数：

| 函数 | 功能 |
| --- | --- |
| `load_task_plan()` | 加载 JSON + 可选 Schema 校验 |
| `load_action_defaults_from_yaml()` | 从 YAML 读取动作参数 |
| `load_capability_model_from_yaml()` | 从 YAML 读取集群能力与资源上限 |
| `build_graph_from_task_plan()` | 将 plan dict 转为 BuiltGraph |
| `build_graph_from_task_plan_file()` | 一站式文件到 BuiltGraph（含环境引用校验） |

`build_graph_from_task_plan_file()` 签名：

```python
def build_graph_from_task_plan_file(
    task_plan_path: str | Path,
    *,
    schema_path: Optional[str | Path] = None,
    action_templates_path: Optional[str | Path] = None,
    capability_model_path: Optional[str | Path] = None,
    environment_config_path: Optional[str | Path] = None,
    segment_id: Optional[str] = None,
) -> BuiltGraph:
```

当 `environment_config_path` 不为 None 时，自动调用 `validate_task_plan_environment()` 校验 scenario_id / actor / target 引用是否存在。

---

### 3.4 `gcjp/environment_model.py`

轻量级环境引用层，提供以下能力：

| 函数 | 功能 |
| --- | --- |
| `load_environment_config()` | 加载环境 YAML |
| `get_scenario()` | 按 scenario_id 查找场景 |
| `resolve_location()` | 将位置引用解析为 x/y 坐标 |
| `euclidean_distance_km()` | 欧几里得距离计算 |
| `estimate_straight_line_metrics()` | 预估飞行耗时与能耗 |
| `validate_environment_refs()` | 校验 actor/target 引用 |
| `validate_task_plan_environment()` | 高层校验入口 |

当前阶段仅做引用校验，不做复杂轨迹可行性分析（禁飞区绕行、威胁区惩罚等）。

---

### 3.5 `gcjp/mission_graph.py`

实现 GCJP 的核心受限 API：

- `TaskNode`：任务节点，含 duration_lb/ub、energy_cost、ammo_cost；
- `Constraint`：约束对象，含 constraint_type 和 params；
- `TaskGraphBuilder`：任务图构建器，支持 `add_task`、`add_dependency` 和结构化约束 API；
- `BuiltGraph`：构建后的只读任务图对象，含 NetworkX 有向图和约束列表。

GCJP v1 的 LLM 可调用白名单集中定义在 `gcjp/api_spec.py`。生成代码不允许直接调用 `add_constraint()`，应使用以下结构化接口：

```text
declare_segment_meta
add_task
add_dependency
add_time_order_constraint
add_time_window_constraint
add_sync_constraint
add_resource_constraint
add_capability_constraint
add_physical_feasibility_constraint
declare_resource_state
declare_interface_fulfillment
build
```

示例用法：

```python
from gcjp.mission_graph import TaskGraphBuilder

g = TaskGraphBuilder(segment_id="seg_demo", assigned_actors=["fleet_1"])
g.add_task(
    task_id="T1", actor="fleet_1", action="reconnaissance",
    target="hq_mark6", duration_lb=2.0, required_capability=["recon_capable"],
    energy_cost=3.0, ammo_cost=0,
)
g.add_dependency("T1", "T3", relation="condition_trigger")
g.add_time_window_constraint("T3", deadline=30.0, source_label="deadline_T3")
built = g.build()
```

---

### 3.6 `gcjp/constraint_templates.py`

将 `BuiltGraph` 中的约束转化为 Z3 表达式并求解。

支持的约束类型：

| 约束类型 | 作用 |
|---|---|
| `time_order` | 顺序依赖：end(before) <= start(after) |
| `duration` | 持续时间：lb <= dur <= ub |
| `time_window` | 时间窗与截止时间 |
| `sync` | 同步开始：\|start_i - start_j\| <= tolerance |
| `resource` | 资源上限：sum(cost) <= max |
| `capability` | 能力匹配：required ⊆ actor_caps |
| `physical_feasibility` | 物理可行性：dur >= dist / speed × 60 |

UNSAT 时使用 `assert_and_track` 提取冲突约束标签，供归因分析使用。

---

### 3.7 `gcjp/code_executor.py`

执行经过安全检查的 GCJP v1 代码字符串，并提取 `built = g.build()` 得到 `BuiltGraph`。

```text
GCJP 代码字符串 → safety_checker → 受限执行 → BuiltGraph
```

执行器只允许导入 `gcjp.mission_graph.TaskGraphBuilder`，并要求最终变量 `built` 是 `BuiltGraph`。

---

### 3.8 `verifier/pipeline.py`

四层递进验证管道统一入口：

```text
Layer 1: GCJP代码执行验证（安全检查 + 受限执行 + BuiltGraph 提取）
Layer 2: 图结构验证（DAG 合法性、连通性、节点覆盖、关键路径）
Layer 3: Z3 约束验证（SAT 输出调度方案 / UNSAT 输出冲突归因）
Layer 4: 语义反向校验（预留接口）
```

`verify_graph()` 对已有 BuiltGraph 运行 Layer 2-4；`verify_gcjp_code()` 对 GCJP 代码字符串运行完整闭环；`verify_code()` 保留为兼容旧接口。

---

### 3.9 `gcjp/debug_logger.py`

全局可控调试日志器。`VERBOSE=False`（默认）时日志只写入内部缓存不输出控制台，`VERBOSE=True` 时同步打印。所有模块通过 `from gcjp.debug_logger import debug` 共享同一实例。

---

## 4. 快速开始

### 4.1 安装依赖

```powershell
python -m pip install networkx z3-solver pyyaml jsonschema
```

建议 Python 3.10+，推荐使用 conda 环境 `llm`。

### 4.2 运行 JSON → 任务图 → 验证（SAT 正例）

```powershell
python -m demos.demo_01_build_graph_from_json
```

预期：总体 PASS，Layer 2/3/4 全部通过。fleet_1 侦察 target_A，fleet_2 待机 target_B 后拦截。

### 4.3 运行资源超限 UNSAT 反例

```powershell
python -m demos.demo_02_build_resource_unsat_from_json
```

预期：总体 FAIL，Layer 2 通过，Layer 3 UNSAT。unsat_core 包含 `resource_fleet_1_ammo`，归因指向 fleet_1 弹药超限（5 次打击 > max_ammo=4）。

### 4.4 运行设施 UTM 场景 + 环境引用校验

```powershell
python -m demos.demo_03_build_facilities_from_json
```

预期：总体 PASS。fleet_1 前出 hq_mark6 侦察，fleet_2 前出 hq_mark7 待机后拦截。环境引用校验通过（scenario_facilities_utm 场景，坐标来自真实 UTM 数据）。

### 4.5 运行手写 GCJP demo

```powershell
python -m demos.demo_01_simple_solo      # SAT 正例
python -m demos.demo_05_unsat_example     # UNSAT 反例
python -m demos.demo_06_fixed_gcjp_api    # GCJP v1 代码字符串端到端验证
```

### 4.6 工具脚本

```powershell
# JSON Schema 格式校验
python tools/validate_task_plan.py schemas/task_plan_schema.json demos/demo_03_facilities_task_plan.json

# 配置文件完整性校验
python tools/validate_configs.py
```

---

## 5. 当前已验证结论

1. 标准化任务计划 JSON 可以通过 schema 约束表达任务节点与依赖关系；
2. `task_plan_loader.py` 可以将标准化 JSON 转换为 `BuiltGraph`，并自动接入外部 YAML 配置；
3. `action_templates.yaml` 提供动作参数白名单，替换硬编码默认值；
4. `capability_model.yaml` 提供异构集群的真实资源上限与能力定义；
5. 资源超限可被 Z3 检测并归因（demo_02：fleet_1 弹药 5 > 4）；
6. 能力不匹配可被 Z3 检测；
7. `environment_model.py` 可校验任务计划中的 scenario_id / actor / target 与环境配置的一致性；
8. `environment_facilities.yaml` 提供了从 UTM 坐标转换的真实设施地图数据；
9. `VerificationPipeline` 完成 SAT/UNSAT 检测并输出包含归因分析的验证报告；
10. `DebugLogger` 实现全局可控调试输出，VERBOSE=False 时静默运行。

---

## 6. 当前仍需完善的问题

### 6.1 环境模型仅做引用校验

`environment_model.py` 当前只校验 actor/target 是否在场景中存在，不做禁飞区绕行、威胁区惩罚、动态障碍物避让等复杂轨迹可行性分析。

### 6.2 物理距离尚未从坐标自动计算

手写 demo 可调用 `add_physical_feasibility_constraint()`，但 JSON → BuiltGraph 路径尚未根据 actor 初始位置和目标点坐标自动计算飞行距离并添加物理可行性约束。

### 6.3 UNSAT 归因仍有底层噪声

当前 UNSAT core 中可能包含 `start_nonneg_*`、`end_def_*` 等框架约束，不适合作为人机交互或修复 Agent 的直接输入。

### 6.4 语义反向校验（Layer 4）尚未实现

当前 Layer 4 只是预留接口，尚未将任务图反向还原为结构化摘要与原始计划对比。

---

## 7. 下一步开发方向

### Step 1：实现 GCJP 代码执行器

新增 `gcjp/code_executor.py`，输入 GCJP 代码字符串，完成安全检查、受限执行，并提取 `built = g.build()` 得到 `BuiltGraph`。

### Step 2：新增 `verify_gcjp_code()`

在 `VerificationPipeline` 中新增 `verify_gcjp_code(code: str)`，实现 L1 安全检查与执行、L2 图结构验证、L3 Z3 验证、L4 语义反向校验的完整闭环。

### Step 3：跑通手写 GCJP 代码字符串闭环

使用手写 GCJP v1 代码字符串验证 `code → BuiltGraph → NetworkX/Z3` 流程，确认代码执行器和验证管道之间的接口稳定。

### Step 4：构建 JSON → LLM → GCJP 实验

使用现有 demo JSON 作为输入，让强模型生成 GCJP v1 代码，并统计代码安全通过率、执行成功率、DAG 合法率和 Z3 结果一致率。

### Step 5：构建 NL → LLM → GCJP 最小闭环

使用简单自然语言指令直接生成 GCJP v1 代码，验证端到端 Code-as-Plan 主线。

---

## 8. 依赖

| 包 | 用途 |
|---|---|
| `networkx` | 任务DAG构建与图结构分析 |
| `z3-solver` | SMT 约束求解与 UNSAT core 提取 |
| `pyyaml` | 读取配置文件 |
| `jsonschema` | 校验标准化任务计划 JSON |

安装：

```powershell
python -m pip install networkx z3-solver pyyaml jsonschema
```

建议 Python 3.10+。
