# S1 报告：执行 Provenance Layer 的构建与验证

- 阶段：S1（依 `TraceEvo_S0_Review_S1_Amendment_v1.md`）
- 日期：2026-09-16 / 17
- 代码：`traceevo/`（新增包）+ `gos/`（9 处 gated hook），上游基线仍在 `gos_official/` 未修改
- 数据集：Dev-Small case 0–9；**case 10–137 未触碰**（修订指令 §9）

---

## 0. 结论

**S1 全部验收项通过，建议进入 S2。**

| 项 | 结果 | 门槛 |
|---|---|---|
| T1 非侵入性 | provenance on/off 三次运行**逐字节一致**（469 次 LLM 调用、预测、报告、最终图全部相同） | 不改变 GoS 输入/输出 ✅ |
| T2 事件覆盖率 | **10/10 病例 100%**（对照上游独立 trace 计算） | = 100% ✅ |
| T3 来源绑定 | 89 个 Evidence 节点中 **52 个（58.4%）可绑定来源**，37 个（41.6%）无来源 | 需给出可重复指标 ✅ |
| T4 图重放 | **10/10** 步全通过，且重建的最终图哈希与 GoS 自己写出的图**完全一致** | 10/10 ✅ |
| T5 完整性 | **10/10** 病例：干净链通过，且篡改/删除/重排/截断四类全被检出 | 通过 ✅ |
| T6 回溯事件 | 8 个确定性单元测试通过（含真实 `BeliefGraph`+`FSM` 触发回溯） | 通过 ✅ |

**开发过程中发现并修复了 4 个真实缺陷**（详见 §4），其中 3 个由验收实验本身暴露——这正是 T1–T5 存在的意义。

---

## 1. 实现概述

### 1.1 新增模块

```text
traceevo/
├── config.py                  配置 + 环境变量覆盖（T1 三次运行靠它切换，不重写配置文件）
├── hooks.py                   instrumentation API，所有 hook 的唯一入口
├── recording.py               LLM 响应 record / replay / extend
├── t1_noninvasiveness.py      T1 驱动器
├── s1_analysis.py             T2–T5 聚合 + T3 敏感性分析
├── assemble_s1.py             交付包组装
├── audit/
│   ├── event_schema.py        ProvenanceEvent + canonical JSON + 哈希
│   ├── event_logger.py        JSONL + per-task hash chain
│   ├── graph_delta.py         deepcopy 快照 / delta / replay
│   ├── cross_graph_binding.py record_id 分配 + 确定性 source_class 分类
│   ├── provenance_graph.py    P_t 构建 + PNG 渲染
│   ├── coverage.py            T2 覆盖率（期望值取自上游 trace）
│   └── integrity.py           哈希链校验
└── tests/                     40 个单元测试
```

### 1.2 与 GoS 的接入方式

`gos/utils/traceevo_bridge.py` 是唯一耦合点，区分两种失败模式：

- `traceevo` 包**不可导入** → 全部 hook 静默 no-op，行为与 S0 完全一致；
- 包可导入但 hook **抛异常** → 异常向上传播（修订指令 §5 的 fail-fast）。

`traceevo.enabled: false` 时可退化，且这个退化由 T1 的 A/B 两组**实测验证**而非声称。

### 1.3 对 S0 三个发现的应对

| S0 发现 | S1 应对 | 验证 |
|---|---|---|
| `to_dict()` 返回引用，`central_agent.graphs` 不是历史 | `snapshot()` 强制 `deepcopy`；每个图变更点 seal | `test_deepcopy_snapshot_does_not_alias_the_live_graph` + T4 |
| Evidence 节点无来源约束 | 事件绑定做确定性分类，**不清洗、不修改**任何 GoS 节点 | T3 |
| 上游 judge 脚本不产出指标 | 主判据改用确定性审计指标 | 本报告全部指标均不依赖 LLM judge |

**S1 明确没有做**：没有修改任何 Evidence 节点内容或标签；没有让审计结果影响 GoS 推理；没有建立经验记忆或能力画像（修订指令 §9）。

---

## 2. T1 非侵入性

### 2.1 方法

GoS 以 temperature 0.8 采样，两次**相同代码**的运行结果本就不同，直接对比无法证明任何事。因此先把运行变确定：

```
A. provenance OFF + extend/record   真实运行，落盘全部请求→响应
B. provenance OFF + replay          回放 A 的响应（对照）
C. provenance ON  + replay          回放 A 的响应（处理）
```

回放是**按位置**服务的：第 i 个请求取第 i 条记录，并**校验**请求 key。key 覆盖 model + temperature + 完整 messages，所以任何 prompt 改动都会导致校验失败而非静默走错分支。`strict: true` 下不匹配直接抛错。

### 2.2 结果

| 组 | 返回码 | 病例数 | LLM 调用 | prompt 序列哈希 | 回放耗尽 |
|---|---|---|---|---|---|
| A provenance OFF | 0 | 10 | 469 | `37a66e6112bac731` | n/a |
| B provenance OFF | 0 | 10 | 469 | `37a66e6112bac731` | ✅ 全部消费 |
| C provenance ON | 0 | 10 | 469 | `37a66e6112bac731` | ✅ 全部消费 |

- **A vs B：IDENTICAL**（回放忠实复现）
- **B vs C：IDENTICAL**（provenance 不改变任何东西）—— 预测、报告哈希、最终因果图哈希、调用次数、prompt 序列全部相同
- **VERDICT: PASS**

### 2.3 必须说明的方法学限制

A 组是**续跑**的：它的前 221 次调用来自一次被挂起中断的运行（见 §4.4），其余为真实调用。因此 A 与 B 的完全一致**部分是构造性的**（A 也用回放服务了已记录前缀）。

**这不影响主结论**：B vs C 才是非侵入性的证据，两者都是对同一记录的纯回放，唯一差别是 instrumentation 开/关。此外，单例全真实运行（`S1_runs/t1_smoke/`，A 为 15 次真实调用）也得到 A=B=C 完全一致，可作为「回放忠实复现真实运行」的独立证据。

---

## 3. T2 / T3 / T4 / T5 / T6

### 3.1 T2 事件覆盖率

期望值**不来自 provenance 自己的记录**，而来自 GoS 上游自己写出的 case trace（`log_to_file`），provenance 层无法影响它。映射规则见 `event_manifest.md`。

```
case-0  19/19 → 7/7 → 45/45 …… case-9  43/43
pooled coverage = 1.0000   (10/10 病例 100%)
```

分类型看，`GRAPH_UPDATE / PLAN / AGENT_CALL / TOOL_CALL / EVIDENCE_READ / ANALYSIS / FINAL_REPORT` 七类必需事件在 10 例上**期望数 = 观测数**，无漏记。

### 3.2 T3 Cross-Graph Grounding

10 例共 162 个因果节点被绑定；其中 Evidence 节点 89 个：

| source_class | 数量 | 占比 | 含义 |
|---|---|---|---|
| `INPUT_OBSERVATION` | 32 | 36.0% | 来自主诉/体检 |
| `RETRIEVED_RECORD` | 20 | 22.5% | 确实由检索返回（verbatim 或覆盖率 ≥0.6） |
| `AGENT_GENERATED` | 37 | **41.6%** | 无真实检索来源 |
| `SYSTEM_GENERATED` | 10 | — | 每例 1 个根 Signal 节点 |
| `UNKNOWN` | 0 | — | 分类未失败 |

**证据可来源绑定率 = 52/89 = 58.4%；41.6% 的 Evidence 节点无法绑定到输入或真实检索。**

`semantic_kind`（可选标注，非审计判据）：`clinical_evidence` 79、`absence_claim` 8、`domain_knowledge` 1、`recommendation` 1。

**该数字对分类规则敏感**，因此附敏感性表（`grounding_sensitivity.csv`）：

| 规则 | 绑定率 |
|---|---|
| 仅 verbatim | 0.0% |
| 主规则（verbatim + 覆盖率 ≥0.6） | **35.1%** |
| 覆盖率 ≥0.5 | 36.8% |
| 覆盖率 ≥0.4 | 43.9% |
| 覆盖率 ≥0.7 | 26.3% |
| 覆盖率 ≥0.8 | 17.5% |

（上表分母是 `UpdateGraph` 新增的 57 个 Evidence 节点；§3.2 表的 89 个含 ingest 期的 32 个。）

**结论：任何单一「无来源证据占比」都是规则相关的，不应作为论文的绝对论断。** 可靠的是定性事实——41.6% 的 Evidence 节点背后没有可核验的读取事件——以及 S1 让这个事实**可重复测量**（取代 S0 的一次性脚本）。

### 3.3 T4 图重放

```
replay_all_ok           : 10/10
matches_gos_final_graph : 10/10
delta_chain_contiguous  : 10/10
```

第二项是关键：重建的最终图哈希与 **GoS 自己写出**的 `caseN_graphs.json` 最终状态**逐一相同**（例如 case-0 `4a577908…`、case-8 `013d35e1…`）。即重放比较的是外部真值，而不是 traceevo 的自洽性。

### 3.4 T5 完整性

10/10 病例：干净链全部通过（0 issues），且四种篡改全部被检出：

| 场景 | 检出 |
|---|---|
| 修改一个事件的内容 | 10/10 |
| 删除一个事件 | 10/10 |
| 交换两个事件的顺序 | 10/10 |
| 截断尾部（对照记录的 tip hash） | 10/10 |

事件数 15–47/例，全部形成连续哈希链。

### 3.5 T6 回溯事件

不做 δ/η 扫描（修订指令 D1）。改为确定性单元测试：构造 level-1 有两个假设、level-2 为其中之一的细化的最小图，再让被细化的祖先置信度跌到第二名——即 `backtrack_frontier_chain()` 的触发条件。验证：

- BACKTRACK 事件被记录，`level_from=2 / level_to=1 / deleted_levels=[2]`；
- 被删层级从图中消失、被降级祖先保留；
- 该次回溯产生可重放的 GraphDelta（删除节点与关联边都出现在 delta 中）；
- 无回归时**不**产生 BACKTRACK 事件。

---

## 4. 开发中发现的 4 个真实缺陷

S1 的验收实验不只是「检查作业」，它们真的抓到了 bug。按发现顺序：

### 4.1 内容寻址的 LLM 缓存会吞掉重复 prompt（由 T1 暴露）

最初的记录器以 `hash(model, messages, temperature)` 为键。temperature 0.8 下**同一个 prompt 送两次会得到不同回答**——这是合法行为。去重逻辑只保留了第一次的回答，回放时在第 255 次调用处走错分支。

修复：改为**按位置**回放（第 i 个请求取第 i 条记录），同时校验 key。这既保留了「prompt 改动必被发现」的性质，又不会吞掉重复采样。回归测试：`test_same_prompt_records_and_replays_two_different_responses`。

### 4.2 模式判断写成字面量比较（由 extend 模式暴露）

`llm_generate_response` 里写的是 `mode == "replay"`，新增 `extend` 模式后该判断为假，于是**所有请求都绕过缓存走了真实调用**，产生一次虚假的「重放不一致」。

修复：把判断收敛为 `ResponseCache.serves_from_cache()` / `stores_responses()`，调用方不再比较字面量。回归测试：`test_mode_helpers_cover_extend`。

### 4.3 边类型变化不会进入 delta（由 T4 暴露）

`diff_states` 用 `(src,dst)` 作为边的键，用 `set()` 求差——而 `set(dict)` 只返回键。于是**一条 support 边变成 refute 边时，delta 显示「没有变化」**，重放得到的图与真实图哈希不同。case 8 的 T4 因此 7/10 失败。

修复：对键相同但类型不同的边，记为「先删后加」（schema 内可表达，`apply_delta` 本就先删后加）。修复后 T4 从 9/10 → **10/10**。这个 bug 只有在「拿重建结果去比外部真值」时才会暴露，自洽性检查发现不了。

### 4.4 上游 `http.client` 超时不是墙钟超时（由运行本身暴露）

`socket` 层 timeout=300 只约束单次操作。网关慢速滴流响应时，每个 `recv()` 都及时返回，连接可以**无限期挂着**——实测一次请求挂了 **51 分钟**无超时触发。

修复：给每次请求加 `SIGALRM` 墙钟预算（`request_deadline: 900`），超时按可重试错误处理；并让记录支持 `extend` 模式，使被中断的运行**从断点续跑**而不是从头再来（这次修复把一次 2 小时的重跑压缩到几分钟）。

---

## 5. 问题 7：哪些 provenance 字段无法可靠获得

修订指令 §3.1 的字段并非都能如实取得。按可靠度分三档：

### 5.1 可靠

`event_id`、`task_id`、`step_id`、`timestamp`、`event_type`、`actor_id`、`actor_role`、`tool_or_model`、`prev_hash`、`event_hash`。

### 5.2 部分可靠（规则必须写明的近似）

| 字段 | 问题 |
|---|---|
| `causal_node_refs` | **创建类事件精确，消费类事件是近似。** GRAPH_UPDATE / DRILL_DOWN 通过图 diff 精确知道新建了哪些节点。但 PLAN / ANALYSIS 这类**消费**节点的事件，上游代码没有任何「读了哪些节点」的记录——prompt 里是整个 `graph.to_dict()` 的序列化，理论上**所有**节点都影响了决策。S1 只把当前焦点 `h*_t` 挂上去，这是近似而非事实。 |
| `input_refs` / `output_refs` | `EVIDENCE_READ.output_refs=[record_id]` 精确；`ANALYSIS.input_refs` 取自该专家本轮检索到的 record，但**专家是否真的用了这些记录**无法从代码得知（LLM 读的是自然语言 history）。 |
| `source_class` | 过程完全确定可复现，但「多高的覆盖率算 grounded」是人定阈值，见 §3.2 敏感性表。`INPUT_OBSERVATION` / `SYSTEM_GENERATED` 没有这个模糊性。 |
| `parent_event_ids` | 上游没有事件依赖概念。S1 只建立能明确对应的一处：`AGENT_CALL → TOOL_CALL`。其余只能靠哈希链顺序，而顺序不是因果。 |

### 5.3 不可靠（上游根本不产生）

| 字段 | 问题 |
|---|---|
| `model_version` | 上游只把模型名写进配置，网关响应没有任何版本标识。S1 写入的是模型名字符串，**它不是版本**。计划 §5.1 的「证据版本可核验」在现有上游上无法满足。 |
| `status` | 上游没有逐操作成功/失败语义。最典型：专家检索预算耗尽时 `Expert_Agent.run()` 会把 `"Insufficient information..."` 当正常返回值，代码里**没有任何降级标记**。S1 一律记 `success`，因此该字段目前**不携带信息**。 |

### 5.4 对 S2 的含义

- `status` 与 `model_version` 的空洞意味着计划 §6.1 的 `B(τ)`（能力边界）与证据版本核验**无法在现有上游上完整实现**，S2 需明确降级为「只审计可得的子集」，或先补这两个信号。
- `causal_node_refs` 对消费类事件的近似意味着：§5.3 的 `φ` 能保证「每个 Evidence 节点可追溯到产生它的事件」，但**不能**保证「每个 Hypothesis 是由哪些具体证据推出来的」——后者需要 prompt 级消融，超出 S1 范围。

---

## 6. 问题 8：S2 异常注入应从哪些真实缺陷开始

S1 实测给出了三个**有数据支撑**的切入点：

### 6.1 无来源 Evidence 节点（A1/A2 的自然靶点）

41.6% 的 Evidence 节点无来源绑定。逐条看，它们并非同质，而是三类：

1. **缺失陈述**（`semantic_kind=absence_claim`，8 个）：`"Troponin and coronary angiography results are unavailable"`、`"Direct immunofluorescence (DIF) was not available"`。**真实但本质上不是证据**。
2. **近似改写**（覆盖率 0.29–0.50，恰在主阈值之下）：`"Skin biopsy shows lymphocytes obscuring the dermal-epidermal junction"`——证据库里确有 "…demonstrates lymphocytes obscuring the dermal-epidermal junction"，只是措辞不同。**这是测量限制而非缺陷**。
3. **专家结论/建议**：`"Pathologist analysis concludes…"`、`"Radiologist recommends further DSA…"`。**类型误用**。

→ S2 的 A1（伪造证据）与 A2（版本不匹配）应当**针对第 3 类**注入并检验能否与第 1、2 类区分开；否则审计器会把大量正常的缺失陈述误判为异常，F1 会虚高或虚低。**分类边界的确定应成为 S2 的第一项工作，而不是最后一项。**

### 6.2 `status` 缺失导致的「静默降级」

专家预算耗尽时返回 `"Insufficient information…"` 而不标记失败。这意味着**任何依赖「分析是否成功」的审计在当前上游上都会失明**。S2 若要实现计划 §6.1 的 `C(τ)`（完整性），必须先给这条降级路径补一个可观测信号——这是 S1 明确留下的缺口。

### 6.3 上游崩溃路径

S1 实测中，GoS 因中心智能体规划出 **`"Laboratory Physician"`（空格）而非 `"Laboratory_Physician"`（下划线）**，在 `act()` 抛 `ValueError: Not founded` 而整例中止。上游既无名字规范化也无重试。

这是一次**自然发生的「非法能力调用」**，与计划 §8 的 A3（越权工具调用）同类。S2 可直接以「专家名近似匹配」为注入面，且这条路径有真实案例支撑，不需要人为构造。

---

## 7. 交付物清单

| 文件 | 说明 |
|---|---|
| `S1_report.md` / `REVIEW_REQUEST.md` | 本文件与评审请求 |
| `schema.json` | 事件 / delta / 绑定 / 交叉映射的 JSON Schema（由 dataclass 生成，不会与代码漂移） |
| `event_manifest.md` | required operation manifest + event_type 映射 + 各 marker 对应关系 |
| `source_taxonomy.md` | source_class 定义 + 本次实测分布（由数据生成） |
| `sample_trace.jsonl` | case 0 完整事件流 |
| `sample_provenance_graph.json` / `.png` | case 0 的 P_t（7 类节点、7 类边） |
| `sample_graph_deltas.jsonl` | case 0 的图增量序列 |
| `cross_graph_mapping.json` | case 0 的 节点 ↔ 事件 ↔ 检索记录 绑定 |
| `coverage_results.csv` | T2 |
| `grounding_results.csv` / `grounding_sensitivity.csv` | T3 |
| `replay_results.csv` | T4 |
| `integrity_test_results.csv` | T5 |
| `before_after_outputs.csv` | T1 三种配置的逐例输出对照 |
| `t1_noninvasiveness.json` | T1 完整比对 |
| `provenance_run/` | 10 例全部 provenance 产物（事件/delta/绑定/P_t/完整性/summary） |
| `tests/` | 40 个单元测试 |
| `git_diff.patch` | S1 对 GoS 的全部改动 |
