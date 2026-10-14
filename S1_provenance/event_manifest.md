# S1 事件清单（required operation manifest）

本文件定义 S1 中「哪些 GoS 操作必须被记录」，以及每个操作对应到 `ProvenanceEvent.event_type`
的映射。T2 的覆盖率检查（`traceevo/audit/coverage.py`）以此为判据。

---

## 1. 为什么 event_type 只有 9 个取值

修订指令 §3.1 固定了 `event_type` 的枚举：

```
PLAN | AGENT_CALL | TOOL_CALL | EVIDENCE_READ | ANALYSIS
GRAPH_UPDATE | BACKTRACK | DRILL_DOWN | FINAL_REPORT
```

修订指令 §4 列出了 9 个推荐 hook 位置，但其中 `extract_frontier()` 没有对应的枚举值。
S1 的处理是：**仍然在该位置植入 hook，但不为它单独发事件**——推理焦点 `h*_t` 作为结构化
payload 附加到紧随其后的 `PLAN` 事件上。这样既覆盖了该 hook 位置，又不擅自扩展枚举。

`FINAL_REPORT` 事件同时承担了「结论」锚点的角色：它的 `output_refs` 指向该结论所依赖的
GoS 因果节点，构成 §3.3 要求的「Final Report 必须绑定其直接依赖的 reasoning state」。

---

## 2. 必需事件（每次运行都必须出现）

| event_type | 触发位置（源码） | 期望条数（独立来源） | 必需 |
|---|---|---|---|
| `GRAPH_UPDATE` | `Central_Agent.ingest()` ×2（basic node / L1 hypo） | 2 | ✅ |
| `GRAPH_UPDATE` | `Central_Agent.run()` UpdateGraph 段结束处 | `trace 中 "Central agent decided to update the graph" 次数` | ✅ |
| `PLAN` | `Central_Agent.plan()` | `trace 中 "Here are the plan of central expert" 次数` | ✅ |
| `AGENT_CALL` | `Central_Agent.act()` 每次派发给专家 | `trace 中 "Step 1 - Decision Making" 次数` | ✅ |
| `TOOL_CALL` | `Expert_Agent.run()` 决定 retrieve 且 query 非空 | `trace 中 "Step N Observations:" 次数` | ✅ |
| `EVIDENCE_READ` | `evidence_retriever()` | 同上 | ✅ |
| `ANALYSIS` | `Expert_Agent.run()` 返回分析（含预算耗尽兜底路径） | `trace 中 "Step 1 - Decision Making" 次数` | ✅ |
| `FINAL_REPORT` | `Central_Agent.run()` 的 `type == 1` 分支 | 1 | ✅ |

## 3. 条件事件（仅当对应行为实际发生）

| event_type | 触发位置 | 期望条数（独立来源） | 必需 |
|---|---|---|---|
| `BACKTRACK` | `backtrack_frontier_chain()` 仅在真正触发时 | `trace 中 "backtracking invoked" 次数` | ❌ 条件 |
| `DRILL_DOWN` | refine 分支（`type == 2`）生成下一层假设后 | `trace 中 refine 响应里 "type": 2 的次数` | ❌ 条件 |

`BACKTRACK` 只在 `invalid_start_level` 被判定出来时发出；「检查了但没有回溯」不产生事件。
这与语义一致：事件表示「发生了回溯」，而不是「检查过是否需要回溯」。

---

## 4. 期望条数的来源为什么是独立的

覆盖率如果从 provenance 自己的记录里推导，就成了自证。因此
`coverage.py` 的 `expected_from_trace()` 解析的是 **GoS 上游自己写的 case trace 文件**
（`gos/logs/.../Case_N/<timestamp>.txt`，由 `utils.public_function.log_to_file` 写出）。

provenance 层不控制、也不影响这份文件，所以「上游 trace 显示发生了 5 次检索，而事件日志
里只有 4 条 EVIDENCE_READ」是一个真实的漏记信号。

### 各 marker 的对应关系

| 上游 trace marker | 含义 | 映射到 |
|---|---|---|
| `generating basic symptom and evidence nodes` | ingest 第 1 次 LLM 调用 | GRAPH_UPDATE ×1 |
| `generating L1 hypos` | ingest 第 2 次 LLM 调用 | GRAPH_UPDATE ×1 |
| `Here are the plan of central expert` | 中心智能体规划 | PLAN ×1 |
| `Step 1 - Decision Making` | 一位专家开始 ReAct 循环 | AGENT_CALL ×1、ANALYSIS ×1 |
| `Step N Observations:` | 一次真实的证据检索返回 | TOOL_CALL ×1、EVIDENCE_READ ×1 |
| `Central agent decided to update the graph` | 一次 UpdateGraph | GRAPH_UPDATE ×1 |
| `backtracking invoked` | 一次回溯 | BACKTRACK ×1 |
| `decide to report or dive deeper` 且响应中 `"type": 2` | 一次下钻 | DRILL_DOWN ×1 |

> 注：`TOOL_CALL` 与 `EVIDENCE_READ` 一一对应，是因为 hook 放在 `if not content: break`
> 之后——只有真正发起检索时才会成对产生。

---

## 5. 覆盖率定义

```
coverage = Σ_required min(observed, expected) / Σ_required expected
```

对**必需**类型要求 `observed >= expected`（多记不算错，少记算漏）。
条件类型不参与分母。开发目标：Dev-Small 上 coverage = 100%。

---

## 6. 已知边界情形

| 情形 | 处理 |
|---|---|
| 专家检索预算耗尽，未产生 ANALYSIS 内容 | 兜底路径仍发一条 `ANALYSIS`（内容为上游的固定文案），保证 AGENT_CALL 与 ANALYSIS 配对 |
| 检索 query 为空导致 `break` | 不发 `TOOL_CALL`/`EVIDENCE_READ`；上游 trace 同样不会出现该次 Observation，两侧一致 |
| `ingest()` 抛异常 | 本次 ingest 的 GRAPH_UPDATE 缺失，覆盖率下降——这是应当暴露的真实缺记 |
| 任务中途崩溃 | `finish_task()` 不执行，该 case 无 summary/replay 产物，S1 报告中列出 |
