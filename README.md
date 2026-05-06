# NL2UAVTaskGraph

> Natural-language-driven and verifiable task graph construction for UAV swarm missions.

本项目面向无人机集群任务规划场景，研究如何将自然语言作战指令转化为结构化任务图，并通过图结构检查与 SMT/Z3 约束验证提升任务编排结果的可靠性。

当前阶段聚焦 **Layer 1：确定性底座**。本层不接入 LLM，不训练模型，而是先验证：在已有标准化任务计划输入的前提下，系统能否完成任务图构建、约束编码、SAT/UNSAT 检测与验证报告输出。

---

## 1. 当前数据流

```text
自然语言作战指令
    ↓
标准化任务计划 JSON
    ↓
task_plan_schema.json 格式校验
    ↓
task_plan_loader.py
    ↓
TaskGraphBuilder
    ↓
BuiltGraph
    ↓
VerificationPipeline
    ↓
图结构验证 + Z3 约束验证 + 验证报告
```

当前已跑通的关键路径为：

```text
demos/demo_01_simple_task_plan.json
    ↓
gcjp/task_plan_loader.py
    ↓
gcjp/mission_graph.py
    ↓
verifier/pipeline.py
    ↓
SAT 验证通过
```

同时，项目也保留了手写 GCJP demo，用于验证 `TaskGraphBuilder` 与 Z3 验证底座是否可靠。

---

## 2. 当前已实现内容

```text
NL2UAVTaskGraph/
│
├── README.md
│
├── schemas/
│   └── task_plan_schema.json          # 标准化任务计划 JSON Schema
│
├── demos/
│   ├── demo_01_simple_task_plan.json  # 标准化任务计划样例
│   ├── demo_01_build_graph_from_json.py
│   ├── demo_01_simple_solo.py         # 手写 GCJP SAT 正例
│   └── demo_05_unsat_example.py       # 手写 GCJP UNSAT 反例
│
├── gcjp/
│   ├── __init__.py
│   ├── mission_graph.py               # TaskGraphBuilder 与 BuiltGraph
│   ├── constraint_templates.py        # Z3 约束构建与求解
│   ├── safety_checker.py              # GCJP 代码白名单检查
│   └── task_plan_loader.py            # 标准化 JSON → BuiltGraph
│
└── verifier/
    ├── __init__.py
    └── pipeline.py                    # 图结构验证 + Z3 验证管道
```

---

## 3. 核心文件说明

### 3.1 `schemas/task_plan_schema.json`

定义标准化任务计划的 JSON 格式，是自然语言解析结果与任务图构建模块之间的接口规范。

它约束以下内容：

- `participants`：参与任务的无人机集群、单机或联合编队；
- `tasks`：任务节点，包括执行主体、动作、目标、条件、预期输出等；
- `relations`：任务边，包括顺序、同步、条件触发、屏障、交接等关系；
- `ambiguities`：自然语言解析中的歧义项；
- `missing_fields`：无法从指令中确定、需要人工确认的信息。

示例流程：

```text
自然语言指令
    ↓
标准化任务计划 JSON
    ↓
task_plan_schema.json 校验
```

---

### 3.2 `demos/demo_01_simple_task_plan.json`

当前第一条标准化任务计划样例，对应一个简单的侦察—待机—拦截任务。

示例任务结构：

```text
T1: scout_team_A reconnaissance R1
T2: strike_team_B standby Z1
T3: strike_team_B intercept enemy_main_group

T1 -> T3: condition_trigger
T2 -> T3: sequence
```

该文件用于验证：

```text
标准化任务计划 JSON → BuiltGraph → VerificationPipeline
```

---

### 3.3 `gcjp/task_plan_loader.py`

将标准化任务计划 JSON 转换为 `TaskGraphBuilder` 调用，并输出 `BuiltGraph`。

当前版本使用内置动作默认参数，例如：

- `reconnaissance` 默认持续时间、能力需求和能量消耗；
- `standby` 默认持续时间和资源消耗；
- `intercept` 默认持续时间、能力需求和弹药消耗。

后续将逐步改为从 `configs/action_templates.yaml` 和 `configs/capability_model.yaml` 读取参数。

---

### 3.4 `gcjp/mission_graph.py`

实现 GCJP 的核心受限 API。

主要组件：

- `TaskNode`：任务节点；
- `DependencyEdge`：任务依赖边；
- `Constraint`：约束对象；
- `TaskGraphBuilder`：任务图构建器；
- `BuiltGraph`：构建后的只读任务图对象。

核心接口：

```python
from gcjp.mission_graph import TaskGraphBuilder

g = TaskGraphBuilder(segment_id="seg_demo", assigned_actors=["scout_team_A"])

g.add_task(
    task_id="T1",
    actor="scout_team_A",
    action="reconnaissance",
    target="R1",
    duration_lb=5.0,
    required_capability=["recon_capable"],
    energy_cost=10.0,
    ammo_cost=0,
)

g.add_dependency("T1", "T3", relation="condition_trigger")

built = g.build()
```

---

### 3.5 `gcjp/constraint_templates.py`

将 `BuiltGraph` 中的任务节点、任务边和约束转换为 Z3 表达式。

当前支持：

| 约束类型 | 作用 |
|---|---|
| `time_order` | 顺序依赖约束 |
| `duration` | 持续时间约束 |
| `time_window` | 时间窗与截止时间约束 |
| `sync` | 同步开始约束 |
| `resource` | 资源上限检查 |
| `capability` | 能力匹配检查 |
| `physical_feasibility` | 抽象物理可行性约束 |

UNSAT 时使用 `assert_and_track` 提取冲突约束标签，为后续冲突归因和修复Agent提供输入。

---

### 3.6 `verifier/pipeline.py`

统一验证入口。

当前验证层次：

```text
Layer 2: 图结构验证
    - DAG 合法性
    - 节点覆盖
    - 孤立节点检查
    - 关键路径计算

Layer 3: Z3 约束验证
    - SAT: 输出调度方案
    - UNSAT: 输出 unsat core 与归因说明

Layer 4: 语义反向校验
    - 当前为预留接口
```

当前 `Layer 1: 代码执行验证` 主要用于后续 LLM 生成 GCJP 代码场景；在直接验证 `BuiltGraph` 时暂时跳过。

---

## 4. 快速开始

### 4.1 安装依赖

```powershell
python -m pip install networkx z3-solver pyyaml jsonschema
```

### 4.2 运行 JSON → 任务图 → 验证 demo

```powershell
python -m demos.demo_01_build_graph_from_json
```

预期结果：

```text
总体结果: ✅ 通过
Layer 2 [图结构验证]: 通过
Layer 3 [Z3 约束验证]: 通过
```

该 demo 表明：标准化任务计划 JSON 已经可以被确定性转换为可验证任务图。

---

### 4.3 运行手写 GCJP SAT demo

```powershell
python -m demos.demo_01_simple_solo
```

预期结果：

```text
Demo 01 总体结果: ✅ 全部通过
```

该 demo 验证手写 GCJP 代码可以构建任务图，并通过 Z3 调度验证。

---

### 4.4 运行手写 GCJP UNSAT demo

```powershell
python -m demos.demo_05_unsat_example
```

预期结果：

```text
总体结果: ❌ 失败
Layer 2 [图结构验证]: 通过
Layer 3 [Z3 约束验证]: 失败
UNSAT Core: ...
```

该 demo 验证不可行约束能够被 Z3 检测，并输出冲突归因。

---

## 5. 当前阶段已验证结论

目前已验证：

1. 标准化任务计划 JSON 可以通过 schema 约束表达任务节点与依赖关系；
2. `task_plan_loader.py` 可以将标准化 JSON 转换为 `BuiltGraph`；
3. `TaskGraphBuilder` 可以构造 NetworkX 任务DAG；
4. `VerificationPipeline` 可以完成图结构验证与 Z3 约束验证；
5. SAT 正例可以输出可行调度；
6. UNSAT 反例可以检测时间窗与物理可行性冲突；
7. 关键路径长度已经按节点持续时间累加计算。

---

## 6. 当前仍需完善的问题

### 6.1 动作参数仍在代码中硬编码

`task_plan_loader.py` 当前使用内置 `ACTION_DEFAULTS`。后续应将动作参数迁移到：

```text
configs/action_templates.yaml
```

并由 loader 读取。

---

### 6.2 能力与资源模型尚未配置化

当前资源上限使用默认大数值。后续应新增：

```text
configs/capability_model.yaml
```

并用于提供：

- 集群能力；
- 弹药上限；
- 能量上限；
- 速度参数；
- 航程预算。

---

### 6.3 环境坐标与物理距离尚未接入 JSON 路径

手写 demo 中已能调用 `add_physical_feasibility_constraint()`，但 JSON → BuiltGraph 路径尚未自动根据目标点坐标计算距离。

后续应新增：

```text
configs/environment_config.yaml
```

并实现：

```text
目标点坐标 → 距离计算 → 物理可行性约束
```

---

### 6.4 UNSAT 归因仍有底层噪声

当前 UNSAT core 中可能包含：

```text
start_nonneg_*
end_def_*
dur_lb_*
```

这些约束对 Z3 求解必要，但不适合作为最终人机交互或修复Agent输入。后续需要增加归因过滤与高层冲突摘要。

---

### 6.5 语义反向校验尚未实现

当前 Layer 4 只是预留接口。后续应将任务图反向还原为结构化摘要，并与标准化任务计划 JSON 进行一致性检查。

---

## 7. 下一步开发方向

### Step 1：配置化动作模板

目标：将 `task_plan_loader.py` 中的 `ACTION_DEFAULTS` 迁移到 `configs/action_templates.yaml`。

开发内容：

```text
configs/action_templates.yaml
    ↓
load_action_defaults_from_yaml()
    ↓
build_graph_from_task_plan_file(..., action_templates_path=...)
```

验收标准：

```powershell
python -m demos.demo_01_build_graph_from_json
```

仍然输出：

```text
总体结果: ✅ 通过
```

---

### Step 2：配置化能力模型

目标：将资源上限与主体能力从默认值迁移到 `configs/capability_model.yaml`。

开发内容：

- 定义 `scout_team_A` 与 `strike_team_B` 的能力和资源；
- loader 根据 `participant.capability_ref` 查找能力；
- 自动添加资源约束；
- 后续支持 capability 检查。

验收标准：

```text
JSON demo 中的 actor 能够自动匹配资源预算与能力定义。
```

---

### Step 3：增加资源不可行 JSON 反例

目标：构造一个从标准化任务计划 JSON 出发的 UNSAT 样例，而不是只依赖手写 GCJP。

建议新增：

```text
demos/demo_02_unsat_from_json.json
demos/demo_02_build_unsat_graph_from_json.py
```

验收标准：

```text
Layer 2 通过
Layer 3 UNSAT
归因指向资源不足或时间窗冲突
```

---

### Step 4：优化 UNSAT 归因输出

目标：过滤底层约束噪声，输出更适合人机交互和修复Agent使用的高层冲突说明。

建议过滤：

```text
start_nonneg_*
end_def_*
```

建议保留：

```text
resource_*
time_window_*
hard_deadline_*
phys_feasibility_*
sequence_*
sync_*
```

---

### Step 5：实现语义反向校验最小版

目标：从 `BuiltGraph` 反向生成任务摘要，并与 `demo_01_simple_task_plan.json` 中的 `tasks` 和 `relations` 对齐检查。

最小检查内容：

- task_id 是否一致；
- actor 是否一致；
- action 是否一致；
- target 是否一致；
- relation source/target/type 是否一致。

---

### Step 6：接入 NL → 标准化任务计划 JSON

当前确定性底座稳定后，再新增：

```text
agents/instruction_parser.py
```

目标：使用 LLM 或规则模板，将自然语言指令转为符合 `task_plan_schema.json` 的标准化 JSON。

接入后的完整流程：

```text
自然语言指令
    ↓
instruction_parser.py
    ↓
task_plan_schema.json 校验
    ↓
task_plan_loader.py
    ↓
TaskGraphBuilder
    ↓
VerificationPipeline
```

---

## 8. 推荐提交粒度

建议按以下粒度提交 Git：

```text
1. Add standardized task plan schema and JSON demo
2. Add deterministic task plan loader
3. Connect task plan JSON to GCJP verification pipeline
4. Add action template configuration
5. Add capability model configuration
6. Add JSON-based UNSAT demo
7. Improve UNSAT attribution report
```

---

## 9. 依赖

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

建议 Python 版本：

```text
Python 3.10+
```
