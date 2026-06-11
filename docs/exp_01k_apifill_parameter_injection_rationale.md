# exp_01k API-fill 确定性参数注入设计说明

## 背景

`exp_01j` 用 sentinel 解决了一部分系统参数幻觉问题：LLM 在
`duration_lb`、`energy_cost`、`ammo_cost`、`required_capability` 等槽位写
`FILL_xxx`，再由 Python AST filler 从 YAML 配置确定性替换。

但 sentinel 方案仍有静默穿透风险：如果 LLM 直接写合法数字或能力列表，
filler 不会替换，也不一定报错，幻觉参数可能进入 BuiltGraph 和 Z3。

## 01k 方法

`exp_01k` 保留 Code-as-Plan 主线，但从 LLM 可见 API 中移除系统参数槽位：

```text
standard NL -> GCJP API-fill code -> execute_gcjp_code
-> TaskGraphBuilder runtime config resolution -> BuiltGraph
-> L2/L3/Z3 verification -> config_param_conformance
```

LLM 只生成任务结构：

```python
g.add_task("t1", actor="fleet_1", action="reconnaissance", target="area_A")
```

系统参数由运行时注入的 `action_templates.yaml` 和 `capability_model.yaml`
确定性绑定：

- `duration_lb`
- `energy_cost`
- `ammo_cost`
- `required_capability`
- resource 上限
- capability 约束

因此 01k 生成的是 config-bound GCJP code，不是完全自包含代码。

## 实验关系

| 实验 | Code-as-Plan | LLM 生成系统参数 | 定位 |
|---|---:|---:|---|
| `exp_01b` | 是 | 是 | 原始基线 |
| `exp_01h` | 是 | 是 | prompt 注入配置表后由 LLM 查表 |
| `exp_01j` | 是 | 否，写 sentinel | sentinel 确定性填参消融 |
| `exp_01k` | 是 | 否，API 无参数槽位 | API 级确定性注入主方法 |
| `exp_01i` | 否 | 否 | JSON IR 路径上界/辅助对照 |

01k 不是 01i 的 JSON 构图路径；它仍然通过 GCJP 代码执行进入
`execute_gcjp_code` 和完整验证管道。

## 防线与指标

01k 在执行前运行 `check_gcjp_apifill_contract`。如果代码包含系统参数、
sentinel、`add_resource_constraint`、`add_capability_constraint` 或动态调用，
实验记录 `PARAM_LEAK` 并短路，不执行代码。

`config_param_conformance_rate` 是 01k 的核心证据：build 后逐 task 对比
BuiltGraph 中的系统参数与 YAML 加载结果。该指标理论上应为 1.0；否则说明
contract checker 或 runtime 注入逻辑存在漏洞。

## 兼容性

共享层改动保持 additive：

- 不传 runtime config 时，旧的完整参数 GCJP 代码仍按原路径执行。
- `exp_01j`、`gcjp/skeleton_filler.py` 和 01j prompt 不修改。
- 新严格 checker 只由 01k 显式调用，不替换旧 `check_gcjp_code`。
- `verify_gcjp_code` 仅新增可选 config 透传参数，默认行为不变。
