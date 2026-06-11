# JSONL 数据集查看器使用指南

> 工具：`tools/jsonl_viewer.html`（纯前端单文件，零依赖）
> 更新时间：2026-06-11
> 用途：本地浏览数据集样本与实验产物，无需起服务、无需安装任何东西

---

## 1. 打开方式

用浏览器直接打开 `tools/jsonl_viewer.html` 即可。加载数据有三种方式：

1. **文件选择**：点击"打开文件"，支持一次多选；
2. **拖放文件**：把一个或多个文件拖进窗口；
3. **拖放整个目录**：把实验 run 目录整个拖进来，查看器递归收集其中所有可加载文件，并自动跳过代码/产物子目录（`initial_code/`、`final_code/`、`raw_outputs/`、`repair_attempts/`、`final_dag/`），只留下报告与数据。

多文件加载时记录跨文件按 `sample_id` 稳定排序，浏览顺序与文件系统枚举顺序无关。

## 2. 支持的输入格式

| 格式 | 说明 |
|------|------|
| `.jsonl` | 逐行一条 JSON 记录（数据集、metrics、报告均可） |
| `.json` | 单对象或数组 |
| `.yaml` / `.yml` | 含 `cases:` 列表的模板（`generate_cases` 产出的 spec、`make_case` 模板）会展开为逐样本浏览 |

## 3. 四种视图

| 视图 | 快捷键 | 说明 |
|------|--------|------|
| 🌳 树形视图 | `t` | 可折叠 JSON 树，适合逐字段细读单条样本 |
| 📋 表状视图 | `f` | 扁平字段表，适合横向扫多个字段 |
| `{ }` Raw JSON | `r` | 原始 JSON 文本 |
| 🔬 报告视图 | — | 面向 `VerificationReport`：分层（L1–L4）展示验证结果。当加载的记录中验证报告占多数时**自动启用**；报告可位于记录顶层 `report`、`final.report` 或 `initial.report` |

## 4. 筛选与导出

- **关键词搜索**：直接输入文本全文匹配；
- **路径表达式**：`evaluation.first_pass:false`、`error:ValueError` 这类 `路径:值` 形式按字段精确筛；
- **快速过滤**：全部 / 通过 / 失败 / 错误 一键切换；
- **tag 过滤**：按样本 tags 聚合出的标签条快速圈定子集；
- **导出过滤结果**：把当前筛中的记录另存为 JSONL。

## 5. 典型工作流

```text
① 审查数据集：把 datasets/v2/phase1_master_cases.jsonl 拖入
   → 树形视图逐条核对字段（配合 docs/dataset_v2_manual_audit_guide.md 的判读口径）

② 审查一次 exp_01l run：把 out/phase1_generation/<run>/exp_01l_.../ 整个目录拖入
   → 自动跳过代码子目录、聚合 reports/ 下逐样本报告、切到报告视图
   → 用 evaluation.first_pass:false 圈出失败样本逐条看分层结论

③ 审查生成器产物：把 tools/dataset/templates/*.yaml 拖入
   → cases 展开逐条浏览 compact spec
```
