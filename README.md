# UAV Task Planning — 确定性底座（第一层）

> 契约驱动多Agent无人机集群任务规划系统 —— 工程实现第一层
> 
> 对应研究计划工程清单 v2 的 **Layer 1**：确定性底座（纯工程，不涉及LLM）

* * *

## 目录

1. [整体架构与数据流](#1-整体架构与数据流)
2. [已实现的文件清单](#2-已实现的文件清单)
3. [各文件的职责与关系](#3-各文件的职责与关系)
4. [快速开始：运行验证样例](#4-快速开始运行验证样例)
5. [如何手写一个新的任务段](#5-如何手写一个新的任务段)
6. [验证管道输出解读](#6-验证管道输出解读)
7. [工程接入指南（接下来的步骤）](#7-工程接入指南接下来的步骤)
8. [依赖安装](#8-依赖安装)

* * *

## 1. 整体架构与数据流

本项目在这一层的核心问题是：

> **在接入任何 LLM 之前，先把"如果有了正确输入，系统能否跑通并验证"这件事完全确定下来。**

数据流如下：

    自然语言作战指令（NL）
            │
            │  [暂时手工完成，后续由 LLM 替代]
            ▼
    ┌─────────────────────────────┐
    │   标准化任务计划 JSON          │  ← schemas/task_plan_schema.json 定义格式
    │   （tasks / relations / ...） │
    └─────────────────────────────┘
            │
            │  [暂时手工完成，后续由指挥Agent替代]
            ▼
    ┌─────────────────────────────┐
    │   分解输出 JSON               │  ← schemas/decomposition_schema.json 定义格式
    │   （segments / contracts / ...）│  含契约接口定义
    └─────────────────────────────┘
            │
            │  按 Layer 0 / Layer 1 拓扑，对每个 segment 分别：
            ▼
    ┌─────────────────────────────┐
    │   GCJP 代码（Python）         │  ← LLM 将在第二层生成此处代码
    │   TaskGraphBuilder API 调用  │    现在由 demos/ 中的手写代码模拟
    └─────────────────────────────┘
            │
            │  safety_checker.py（白名单检查）
            ▼
    ┌─────────────────────────────┐
    │   BuiltGraph 对象             │  ← mission_graph.py 的 build() 输出
    │   （NetworkX DiGraph +        │
    │    Constraint 列表 +          │
    │    ContractFulfillment 列表）  │
    └─────────────────────────────┘
            │
            ▼
    ┌─────────────────────────────────────────────────────┐
    │                四层验证管道                            │
    │                                                     │
    │  Layer 1: 代码执行验证（subprocess 沙箱）               │
    │      ↓ 通过                                          │
    │  Layer 2: 图结构验证（DAG 合法性 / 关键路径）             │
    │      ↓ 通过                                          │
    │  Layer 3: Z3 约束验证                                 │
    │      ├─ SAT  → 提取时间调度方案                        │
    │      └─ UNSAT → unsat core → 归因 → [修复Agent]       │
    │      ↓ 通过                                          │
    │  Layer 4: 语义反向校验（预留接口）                       │
    └─────────────────────────────────────────────────────┘
            │
            ▼
      VerificationReport（JSON 结构化报告）
      + 时间调度方案（每个任务的 start / end / duration）

* * *

## 2. 已实现的文件清单

    uav_task_planning/
    │
    ├── README.md                         ← 本文件
    │
    ├── configs/                          ← 静态配置（不涉及LLM，人工维护）
    │   ├── capability_model.yaml         ← 各集群的异构能力定义
    │   ├── action_templates.yaml         ← 合法动作白名单及参数
    │   └── environment_config.yaml       ← 场景坐标/威胁区/会合点
    │
    ├── schemas/                          ← JSON Schema 格式定义
    │   ├── task_plan_schema.json         ← 标准化任务计划格式
    │   └── decomposition_schema.json     ← 指挥Agent分解输出格式
    │
    ├── gcjp/                             ← GCJP 核心库（LLM 生成代码调用的 API）
    │   ├── mission_graph.py              ← TaskGraphBuilder 受限API主类
    │   ├── constraint_templates.py       ← Z3 约束模板注册与求解
    │   └── safety_checker.py             ← API 白名单静态分析
    │
    ├── verifier/                         ← 四层验证管道
    │   └── pipeline.py                   ← VerificationPipeline 统一入口
    │
    └── demos/                            ← 手写端到端样例（代替LLM生成）
        ├── demo_01_simple_solo.py        ← ✅ 2集群线性任务（SAT路径）
        └── demo_05_unsat_example.py      ← ✅ 物理不可行反例（UNSAT路径）

**待完成（清单 1.6 剩余）：**

    demos/
        ├── demo_02_sync_rendezvous.py    ← 2集群 + 同步会合点
        ├── demo_03_coalition.py          ← 3集群 + 联合段
        └── demo_04_full_complexity.py    ← 4集群 15+节点完整场景

* * *

## 3. 各文件的职责与关系

### 3.1 `configs/capability_model.yaml`

**是什么：** 描述系统中每个集群（fleet）的能力上限和资源预算。

**被谁读：** 目前由手写的 demo 代码直接引用参数数值。后续由指挥Agent（`agents/commander_agent.py`）在生成契约时读取，以进行物理可行性预估。

**关键字段：**

    fleet_1:
      fleet_constraints:
        cruise_speed_kmh: 80    # 用于 physical_feasibility 约束的速度参数
        max_ammo: 4             # 用于 add_resource_constraint("fleet_1", "ammo", 4)
        max_energy_kwh: 50.0    # 用于 add_resource_constraint("fleet_1", "energy_kwh", 50.0)
        recon_capable: true     # 用于 required_capability=["recon_capable"] 的能力检查

**与代码的对应关系：**

    capability_model.yaml           →   TaskGraphBuilder.add_task() 的参数来源
      fleet_1.fleet_constraints          duration_lb 参数（由 min_task_duration 推导）
      fleet_1.fleet_constraints          add_resource_constraint() 的 max_value
      fleet_1.fleet_constraints          add_physical_feasibility_constraint() 的 speed 参数

* * *

### 3.2 `configs/action_templates.yaml`

**是什么：** 所有合法动作的参数模板，是 GCJP API 的动作白名单。

**被谁读：** 后续由 `gcjp/safety_checker.py` 验证 LLM 生成代码中的 `action` 参数合法性；由 LLM 的 System Prompt 引用，告诉 LLM 哪些动作可用。

**关键字段：**

    reconnaissance:
      min_duration: 2.0         # → add_task(..., duration_lb=2.0)
      resource_cost:
        energy_kwh: 3.0         # → add_task(..., energy_cost=3.0)
        ammo: 0                 # → add_task(..., ammo_cost=0)
      required_capabilities:
        - recon_capable          # → add_task(..., required_capability=["recon_capable"])

* * *

### 3.3 `configs/environment_config.yaml`

**是什么：** 场景中各目标点的坐标、威胁区、会合点定义。

**被谁读：** 后续用于计算 `add_physical_feasibility_constraint()` 中的 `distance_km` 参数（由两点坐标之差推导）。

**如何计算距离：**

    import math, yaml
    env = yaml.safe_load(open("configs/environment_config.yaml"))["scenarios"]["scenario_demo"]
    pts = env["target_points"]
    
    def dist_km(a_id, b_id):
        a, b = pts[a_id], pts[b_id]
        return math.sqrt((a["x"]-b["x"])**2 + (a["y"]-b["y"])**2)
    
    print(dist_km("hq_mark6", "hq_mark7"))  # → 7.07 km

* * *

### 3.4 `schemas/task_plan_schema.json`

**是什么：** 标准化任务计划的 JSON Schema，是 NL 解析器输出和指挥Agent输入之间的数据契约。

**被谁读：** 后续由 `agents/instruction_parser.py` 校验 LLM 输出的结构化任务计划是否符合格式。

**如何校验：**

    import json, jsonschema
    
    schema = json.load(open("schemas/task_plan_schema.json"))
    plan = json.load(open("your_plan.json"))
    jsonschema.validate(plan, schema)  # 不抛出异常则格式正确

**核心字段结构：**

    {
      "plan_id": "plan_001",
      "participants": [{"actor_id": "fleet_1", "type": "fleet"}],
      "tasks": [
        {
          "task_id": "t1_recon_mark9",
          "actor": "fleet_1",
          "action": "reconnaissance",   ← 必须在 action_templates.yaml 白名单中
          "target": "hq_mark9"
        }
      ],
      "relations": [
        {"source": "t1_recon_mark9", "target": "t2_fly_to_mark8", "type": "sequence"}
      ],
      "ambiguities": [],               ← 歧义检测结果，非空时触发 HITL
      "parse_confidence": 0.95
    }

* * *

### 3.5 `schemas/decomposition_schema.json`

**是什么：** 指挥Agent分解输出的格式，定义了 segments（段列表）、topology（段间拓扑）和 segment_interfaces（契约接口匹配表）。

**与 `mission_graph.py` 的关系：**

    decomposition_schema.json                  mission_graph.py
    ─────────────────────────────────────────────────────────
    segments[i].segment_id              →  TaskGraphBuilder(segment_id=...)
    segments[i].assigned_actors         →  TaskGraphBuilder(assigned_actors=...)
    segments[i].contract.assumptions    →  declare_segment_meta(assumed_conditions=...)
    segments[i].contract.guarantees     →  declare_contract_fulfillment(guaranteed_conditions=...)
    segments[i].contract.time_budget    →  add_constraint("time_window", deadline=...)
    segments[i].contract.resource_budget→  add_resource_constraint(max_value=...)
    topology.edges[i].dependency_type   →  add_dependency(relation=...)

* * *

### 3.6 `gcjp/mission_graph.py` — TaskGraphBuilder

**是什么：** LLM 生成的 GCJP 代码唯一允许调用的 API。LLM 不能直接操作 NetworkX，不能访问文件系统，只能调用这里定义的白名单方法。

**核心使用模式：**

    from gcjp.mission_graph import TaskGraphBuilder
    
    # 1. 创建 Builder（对应一个 segment）
    g = TaskGraphBuilder(segment_id="seg_fleet1_solo", assigned_actors=["fleet_1"])
    
    # 2. 声明段元信息（契约的"假设"侧）
    g.declare_segment_meta(
        assumed_conditions=["fleet_1 at initial position"],
        contract_ids_to_fulfill=["contract_fleet1_to_coalition"]
    )
    
    # 3. 添加任务节点
    g.add_task(
        task_id="t1_recon_mark9",       # 唯一ID
        actor="fleet_1",                # 必须在 assigned_actors 中
        action="reconnaissance",        # 必须在 action_templates 白名单中
        target="hq_mark9",
        duration_lb=2.0,                # 从 action_templates.yaml 中读取
        required_capability=["recon_capable"],
        energy_cost=3.0,
        ammo_cost=0,
    )
    
    # 4. 添加依赖边（自动注册对应Z3约束）
    g.add_dependency("t1_recon_mark9", "t2_fly_to_mark8", relation="sequence")
    
    # 5. 添加物理可行性约束（从 environment_config.yaml 读取距离）
    g.add_physical_feasibility_constraint(
        task_id="t2_fly_to_mark8",
        from_position="hq_mark9", to_position="hq_mark8",
        distance_km=15.0,               # 从 environment_config 计算
        actor_speed_kmh=80.0,           # 从 capability_model 读取
    )
    
    # 6. 添加资源约束（从 capability_model.yaml 读取上限）
    g.add_resource_constraint("fleet_1", "ammo", max_value=4)
    g.add_resource_constraint("fleet_1", "energy_kwh", max_value=50.0)
    
    # 7. 声明出口状态（供整合器和下游段使用）
    g.declare_resource_state("fleet_1", remaining_ammo=4,
                              remaining_energy=45.0, position="hq_mark8")
    
    # 8. 声明契约履行（对应分解JSON中的 guarantees）
    g.declare_contract_fulfillment(
        interface_id="contract_fleet1_to_coalition",
        exit_node="t2_fly_to_mark8",
        resource_state={"fleet_1": {"ammo": 4, "energy_kwh": 45.0}},
        guaranteed_conditions=["fleet_1 completed recon of hq_mark9",
                                "fleet_1 at hq_mark8"],
    )
    
    # 9. 构建（返回 BuiltGraph 供验证管道使用）
    built = g.build()

**关键约束：`add_dependency()` 会自动注册对应 Z3 约束**

* `relation="sequence"` → 自动注册 `time_order` 约束（end_source ≤ start_target）
* `relation="sync"` → 自动注册 `sync` 约束（|start_i - start_j| ≤ tolerance）
* 时间窗口参数 → `add_task()` 内自动注册 `time_window` 约束
* 不需要手动调用 `add_constraint()` 处理这些基本场景

* * *

### 3.7 `gcjp/constraint_templates.py` — Z3ConstraintBuilder

**是什么：** 将 `BuiltGraph.constraints` 列表中的逻辑约束转化为 Z3 求解器表达式。**由验证管道自动调用，不需要手动使用。**

**支持的7种约束类型：**

| constraint_type | 含义  | Z3 表达式 |
| --- | --- | --- |
| `time_order` | 顺序依赖 | `end[before] ≤ start[after]` |
| `duration` | 持续时间范围 | `lb ≤ dur[t] ≤ ub` |
| `time_window` | 时间窗 / 截止时间 | `earliest ≤ start[t]`, `end[t] ≤ deadline` |
| `sync` | 同步约束 | `   |
| `resource` | 资源上限 | `sum(cost) ≤ max_value`（Python层预检） |
| `capability` | 能力匹配 | `required ⊆ actor.caps`（Python层预检） |
| `physical_feasibility` | 物理可行性 | `dur[t] ≥ dist/speed` |

**UNSAT 归因机制：** 使用 Z3 的 `assert_and_track` 给每条约束挂一个追踪布尔变量，UNSAT 时调用 `solver.unsat_core()` 获取冲突约束集合，再由 `_attribute_unsat_core()` 翻译为中文说明。

* * *

### 3.8 `gcjp/safety_checker.py` — SafetyChecker

**是什么：** 在执行 LLM 生成的 GCJP 代码之前，用 AST 静态分析检查其是否只调用了受限 API。

**两层检查：**

1. **文本扫描**：检测 `import os`、`eval(`、`exec(`、`open(` 等危险字符串
2. **AST 分析**：解析代码语法树，检查所有方法调用是否在 `ALLOWED_BUILDER_METHODS` 白名单中

**使用方式：**

    from gcjp.safety_checker import check_gcjp_code
    
    code = """
    from gcjp.mission_graph import TaskGraphBuilder
    g = TaskGraphBuilder(segment_id="seg_1", assigned_actors=["fleet_1"])
    g.add_task("t1", actor="fleet_1", action="reconnaissance", ...)
    """
    
    result = check_gcjp_code(code)
    print(result.summary())  # ✅ 安全检查通过 / ❌ 安全检查失败

* * *

### 3.9 `verifier/pipeline.py` — VerificationPipeline

**是什么：** 四层验证管道的统一入口，串联 Layer 1-4。

**两种调用方式：**

    from verifier.pipeline import VerificationPipeline
    
    pipeline = VerificationPipeline(z3_timeout_ms=15_000)
    
    # 方式A：直接验证 BuiltGraph（已有图对象时）
    report = pipeline.verify_graph(built_graph)
    
    # 方式B：验证 LLM 生成的代码字符串 + 对应图（完整四层）
    report = pipeline.verify_code(llm_generated_code, built_graph)
    
    # 输出报告
    report.print_report()            # 打印到控制台
    report_dict = report.to_dict()   # 转为字典（可序列化为JSON）
    
    # 检查结果
    if report.overall_passed:
        schedule = report.schedule   # {task_id: {"start": float, "end": float}}
    else:
        core = report.unsat_core     # 冲突约束标签列表
        attr = report.attribution    # 中文归因说明列表

* * *

## 4. 快速开始：运行验证样例

### 环境准备

    # 克隆/复制项目后，在项目根目录安装依赖
    pip install networkx z3-solver pyyaml jsonschema
    
    # 创建必要的 __init__.py
    touch gcjp/__init__.py verifier/__init__.py

### 运行 Demo 01（SAT 路径）

    python demos/demo_01_simple_solo.py

预期输出：

    Demo 01 总体结果: ✅ 全部通过
    
    fleet_1 时间调度:
      t1_recon_targetA: start=0.00, end=2.00, dur=2.00
      t2_fly_to_targetB: start=2.00, end=9.50, dur=7.50
    
    fleet_2 时间调度:
      t1_fleet2_fly_targetB: start=0.00, end=12.10, dur=12.10
      t2_fleet2_strike_targetB: start=12.10, end=13.10, dur=1.00

### 运行 Demo 05（UNSAT 路径）

    python demos/demo_05_unsat_example.py

预期输出（包含归因）：

    ❌ Layer 3 [Z3 约束验证]: 失败
       → 约束不可满足（UNSAT），10 条冲突约束
    
    归因分析:
      → 物理不可行: 任务 't1_fly_to_mark6' 的时间预算不足以完成飞行距离
      → 顺序冲突: 任务 't1_fly_to_mark6' 必须在 't2_jam_mark6' 之前完成
      → 约束冲突: hard_deadline_30min_t3_deadline
      ...

* * *

## 5. 如何手写一个新的任务段

以下是写一个新 demo 的完整步骤，以"demo_02 同步会合"为例。

### Step 1：查阅配置，确认参数

    import yaml, math
    
    # 读取集群能力
    cap = yaml.safe_load(open("configs/capability_model.yaml"))
    fleet2_speed = cap["fleets"]["fleet_2"]["fleet_constraints"]["cruise_speed_kmh"]  # 100
    fleet2_ammo  = cap["fleets"]["fleet_2"]["fleet_constraints"]["max_ammo"]          # 6
    
    # 读取环境坐标，计算距离
    env = yaml.safe_load(open("configs/environment_config.yaml"))
    pts = env["scenarios"]["scenario_demo"]["target_points"]
    rv  = env["scenarios"]["scenario_demo"]["rendezvous_points"]
    
    def dist(a, b):
        return math.sqrt((a["x"]-b["x"])**2 + (a["y"]-b["y"])**2)
    
    d = dist({"x": 2, "y": -1}, rv["rv_alpha"])   # fleet_2 初始位置到会合点
    flight_time = (d / fleet2_speed) * 60          # 转换为分钟

### Step 2：构建 TaskGraphBuilder

    from gcjp.mission_graph import TaskGraphBuilder
    
    g = TaskGraphBuilder(
        segment_id="seg_fleet2_to_rendezvous",
        assigned_actors=["fleet_2"],
    )
    g.declare_segment_meta(
        assumed_conditions=["fleet_2 at initial position"],
        contract_ids_to_fulfill=["contract_fleet2_sync"],
    )
    
    g.add_task("t1_fly_to_rv_alpha", actor="fleet_2", action="fly_to",
               target="rv_alpha", duration_lb=flight_time,
               required_capability=[], energy_cost=d*0.2, ammo_cost=0)
    
    g.add_physical_feasibility_constraint(
        "t1_fly_to_rv_alpha", "fleet2_init", "rv_alpha",
        distance_km=d, actor_speed_kmh=fleet2_speed
    )
    g.add_resource_constraint("fleet_2", "ammo", max_value=fleet2_ammo)
    g.add_resource_constraint("fleet_2", "energy_kwh", max_value=60.0)
    
    g.declare_resource_state("fleet_2", remaining_ammo=6,
                              remaining_energy=60.0-d*0.2, position="rv_alpha")
    g.declare_contract_fulfillment(
        interface_id="contract_fleet2_sync",
        exit_node="t1_fly_to_rv_alpha",
        resource_state={"fleet_2": {"ammo": 6}},
        guaranteed_conditions=["fleet_2 at rv_alpha"],
    )
    built = g.build()

### Step 3：运行验证

    from verifier.pipeline import VerificationPipeline
    
    pipeline = VerificationPipeline()
    report = pipeline.verify_graph(built)
    report.print_report()

* * *

## 6. 验证管道输出解读

### SAT（通过）时

    report.overall_passed  # True
    report.schedule        # 每个任务的时间调度
    # {
    #   "t1_recon_targetA": {"start": 0.0, "end": 2.0, "duration": 2.0},
    #   "t2_fly_to_targetB": {"start": 2.0, "end": 9.5, "duration": 7.5}
    # }

`schedule` 可以直接送给调度执行层，告诉每个集群何时开始执行哪个任务。

### UNSAT（失败）时

    report.overall_passed  # False
    report.unsat_core      # 冲突的约束标签列表（Z3 原始标签）
    report.attribution     # 中文归因说明（供操作员和修复Agent使用）
    # [
    #   "物理不可行: 任务 't1_fly_to_mark6' 的时间预算不足以完成飞行距离",
    #   "顺序冲突: 任务 't1_fly_to_mark6' 必须在 't2_jam_mark6' 之前完成",
    #   "约束冲突: hard_deadline_30min_t3_deadline"
    # ]

`attribution` 是后续**修复Agent**（`agents/repair_agent.py`）的输入，告诉它哪里出了问题、需要修改哪些约束或重新分配哪段任务。

### 各层独立读取

    for layer_result in report.layers:
        print(f"Layer {layer_result.layer}: {'通过' if layer_result.passed else '失败'}")
        print(f"  详细: {layer_result.details}")
        if layer_result.error_msg:
            print(f"  错误: {layer_result.error_msg}")

* * *

## 7. 工程接入指南（接下来的步骤）

### 当前状态（Layer 1 完成后）

    手写代码（demos/）
        → TaskGraphBuilder API
        → BuiltGraph
        → VerificationPipeline
        → VerificationReport

### Layer 2：接入强模型 LLM（Few-shot）

替换"手写代码"这一步，让 LLM 生成 GCJP 代码：

    # agents/planner_agent.py（即将实现）
    import anthropic
    
    def generate_gcjp_code(task_plan_json: dict, segment_meta: dict) -> str:
        """给LLM看 System Prompt（含API文档）+ 输入JSON，生成 GCJP 代码"""
        client = anthropic.Anthropic()
        response = client.messages.create(
            model="claude-opus-4-6",
            system=PLANNER_SYSTEM_PROMPT,   # 含 TaskGraphBuilder API 文档
            messages=[{
                "role": "user",
                "content": f"任务段信息：{json.dumps(segment_meta)}\n任务计划：{json.dumps(task_plan_json)}"
            }]
        )
        return response.content[0].text   # LLM 生成的 GCJP 代码字符串

LLM 生成的代码通过 `pipeline.verify_code(code, built_graph)` 进入完整四层验证。如果 UNSAT，归因结果再喂回 `repair_agent.py` 进行修复。

接入时**不需要修改**本层任何文件，只需在 `agents/` 目录下新增 `planner_agent.py`，调用已有的 `VerificationPipeline`。

### Layer 3：接入指挥Agent

在 `agents/commander_agent.py` 中实现：NL → 标准化任务计划JSON → 分解JSON，输出需通过 `schemas/decomposition_schema.json` 的格式校验，然后按分解结果调用规划Agent。

整合器（`integrator/`）：将各段的 `BuiltGraph` 拼接为全局图，负责验证 `segment_interfaces` 中的契约对齐（guarantees vs assumptions）。

* * *

## 8. 依赖安装

    pip install networkx z3-solver pyyaml jsonschema

| 包   | 用途  | 在哪里使用 |
| --- | --- | --- |
| `networkx` | 有向无环图（DAG）构建与分析 | `mission_graph.py`, `pipeline.py` (Layer 2) |
| `z3-solver` | SMT 约束求解与 unsat core | `constraint_templates.py`, `pipeline.py` (Layer 3) |
| `pyyaml` | 读取 configs/ 中的配置文件 | demos/ 中读取集群能力和环境坐标 |
| `jsonschema` | 校验 JSON 输出是否符合 Schema | `schemas/` 的格式验证 |

**Python 版本：** 3.9+（使用了 `list[str]` 类型注解语法）

* * *

## 附：约束标签命名规范

UNSAT 归因时，约束标签（`source_label`）遵循以下命名规范，便于阅读：

| 标签前缀 | 来源  | 示例  |
| --- | --- | --- |
| `seq_{A}__{B}` | `add_dependency(relation="sequence")` 自动生成 | `seq_t1__t2` |
| `sync_{A}__{B}` | `add_dependency(relation="sync")` 自动生成 | `sync_t3__t4` |
| `resource_{actor}_{type}` | `add_resource_constraint()` 自动生成 | `resource_fleet_1_ammo` |
| `phys_feasibility_{task}` | `add_physical_feasibility_constraint()` | `phys_feasibility_t1_fly` |
| `time_window_auto_{task}` | `add_task(time_window_earliest=...)` 自动生成 | `time_window_auto_t2` |
| `hard_deadline_{name}` | `add_constraint("time_window", deadline=...)` 手动命名 | `hard_deadline_30min_t3` |
| `dur_lb_{task}` | `_init_variables()` 基础约束自动生成 | `dur_lb_t1_recon` |
| `start_nonneg_{task}` | `_init_variables()` 基础约束自动生成 | `start_nonneg_t1` |

命名时建议**在 `source_label` 中包含语义信息**（如时间数值、集群名），以便归因时直接传递给操作员，无需二次查表。