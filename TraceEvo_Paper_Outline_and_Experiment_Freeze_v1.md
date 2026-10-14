# TraceEvo 论文大纲与实验表格冻结 v1.0
## ——基于 S0–S5B 全部已完成实验的投稿写作蓝图

> 状态：**论文结构冻结 / 实验口径冻结 / 不再新增算法与病例实验**
>
> 数据依据：TraceEvo 当前最终交付包 `TraceEvo(8).zip`，重点使用 `S5B_final_heldout/` 的 frozen artifacts，并结合 S3B–S5A 的阶段性负结果与方法演进记录。
>
> 本文件之后允许做：统计复核、图表美化、文字撰写、复现脚本整理。  
> 本文件之后不允许做：修改 Core-v1、重跑 case 80–137、补新的自然病例、调整 Judge/Gate/P0–P3 阈值、重新启用 Memory/ATGG/role routing。

---

# 1. 论文最终定位

## 1.1 一句话主线

**TraceEvo 在 GoS 医学多智能体推理的因果图之外构建可重放的执行溯源图，并利用来源感知审计与任务后结果反馈联合筛选可复用经验，使“推理过程可追溯”和“经验更新可信”形成闭环。**

论文不是要证明：

> 记忆或自适应机制持续提高医学诊断准确率。

论文要证明：

> **只有结果正确性与过程可信性同时满足，历史多智能体轨迹才应进入可复用经验；全过程 provenance 是完成这一判断的必要基础。**

---

# 2. 标题冻结

## 2.1 首选中文题目

**TraceEvo：面向医学多智能体协同推理的双图审计溯源与可信经验固化方法**

这个题目和当前证据最一致：

- “双图”对应 GoS Causal Graph + TraceEvo Provenance / Invocation Graph；
- “审计溯源”对应 S1/S2/S5B；
- “可信经验固化”对应 S3B/S3C/S5B；
- 不声称 Memory 提升诊断；
- 不声称 role routing / ATGG 已得到效果验证。

## 2.2 不带系统名的备选

**面向医学多智能体协同推理的双图审计溯源与可信经验固化方法**

## 2.3 面向专题的场景化备选

**面向边云医学多智能体协同推理的双图审计溯源与可信经验固化方法**

注意：

“边云”只能作为应用场景与部署动机使用。当前实验没有比较 edge/cloud 调度、带宽或隐私算法，因此正文不得把“边云优化”写成已验证贡献。

## 2.4 英文题目

**TraceEvo: Dual-Graph Auditable Provenance and Outcome-Aware Trustworthy Experience Consolidation for Medical Multi-Agent Reasoning**

---

# 3. 摘要写作冻结

## 3.1 摘要应按五句逻辑写

### 句 1：问题

医学多智能体协同推理能够组合不同专家与外部医学证据，但现有方法通常关注最终推理图或任务结果，难以回答：

- 某一结论由哪些智能体调用和证据产生；
- 中间异常如何传播；
- 一条结果正确的历史轨迹是否真的值得被后续系统复用。

### 句 2：方法

提出 TraceEvo，在 GoS 因果推理图之外构建：

- execution provenance graph；
- model invocation ledger；
- graph-delta / cross-graph binding；

并建立 P0–P3 Evidence provenance tier 与异常审计。

### 句 3：经验 Gate

引入 post-hoc Outcome Verification，与过程审计联合形成：

```text
Validated Success
Validated Failure
Manual Review
Reject
```

四类经验状态。

### 句 4：主要结果

摘要可使用以下最终 held-out 数字：

```text
58-case held-out operational block
402 controlled primary injections
Macro-F1 = 0.960, 95% CI [0.943, 0.975]
Observable-root Top-1 = 0.978 (393/402)

incorrect-clean:
AuditOnly 22/22 admitted
TraceEvo 0/22 admitted

correct-but-poisoned:
OutcomeOnly 19/19 admitted
TraceEvo 0/19 admitted
```

### 句 5：意义

TraceEvo 不依赖修改基础模型参数，而是为多智能体系统提供：

> 可追溯、可审计、结果—过程双约束的经验更新基础。

---

## 3.2 摘要中不要出现

不建议放：

- Match = 0.086；
- Relevant = 0.379；
- “显著提高诊断准确率”；
- “持续自进化性能提升”；
- Role Profile；
- ATGG；
- Success Memory 行为实验。

Match/Relevant 应在实验正文中透明报告，但它不是 TraceEvo 的性能贡献。

---

# 4. 关键词冻结

建议：

```text
医学多智能体系统
协同推理
审计溯源
异常检测
可信经验
大语言模型智能体
```

若专题编辑需要突出边缘智能，可将最后一项替换为：

```text
边缘智能协同计算
```

但正文仍需说明边缘/边云是应用背景，而非本研究已验证的部署优化模块。

---

# 5. 三个贡献点冻结

论文只写三个贡献点。

## Contribution 1：双图耦合的全过程可审计推理

在 GoS 原有 Causal Graph 之外构建执行 provenance：

```text
Provenance Event
Model Invocation Ledger
Graph Delta
Execution Dependency DAG
Cross-Graph Binding
```

解决两类不同问题：

```text
Causal Graph:
为什么得到该医学结论？

Provenance Graph:
谁、何时、调用什么模型/工具、读取什么记录、
通过哪些操作产生该中间状态？
```

创新重点不是“又建了一张图”，而是：

> **在不改变 GoS 正常推理行为的前提下，将语义推理结构和实际执行链条显式绑定，并可重放验证。**

---

## Contribution 2：来源感知的协作异常审计与根因追溯

定义 Evidence provenance tier：

```text
P0 DIRECT_GROUNDED
P1 SOFT_GROUNDED
P2 DERIVED
P3 UNSUPPORTED_ASSERTION
```

结合：

- trace integrity；
- invocation state；
- capability invocation；
- cross-graph binding；
- dependency path；

检测并定位：

- unsupported evidence；
- evidence misbinding/tampering；
- invalid capability invocation；
- trace tampering。

论文最终 headline：

```text
controlled fault model Macro-F1 = 0.960
Observable-root Top-1 = 0.978
```

必须加限定：

> 这是针对本文定义并由 injector 实现的 controlled fault model 的结果，不代表真实世界所有医学智能体异常。

---

## Contribution 3：结果—过程双信号的可信经验固化

提出 Experience Eligibility Contract，将：

```text
Outcome Verification
+
Process Audit
```

联合用于经验准入。

Held-out 双反例：

```text
错误结果 + 过程干净
AuditOnly 22/22 接纳
TraceEvo 0/22

正确结果 + 过程污染
OutcomeOnly 19/19 接纳
TraceEvo 0/19
```

核心论断：

> **结果正确性与执行可信性是经验可复用性的互补条件，任何单一信号都存在结构性盲点。**

---

# 6. Research Questions 冻结

建议最终论文使用 4 个 RQ。

## RQ1：TraceEvo 能否在不改变原始 GoS 推理行为的情况下建立完整、可重放的双图 provenance？

对应证据：

- Core ON/OFF strict replay；
- 58/58 seals；
- hash chain；
- graph replay；
- invocation ledger；
- local overhead。

---

## RQ2：来源感知审计能否检测不同类型的协作异常并定位可观测根因？

对应证据：

- A1/A2/A3/A5 controlled anomaly；
- clean alerts；
- Macro / Micro F1；
- observable-root Top-1；
- latent-root applicable subset；
- secondary findings。

---

## RQ3：为什么可信经验准入必须联合任务结果与过程审计？

对应证据：

- Naive；
- OutcomeOnly；
- AuditOnly；
- IntegrityOnly；
- TraceEvo；
- incorrect-clean；
- correct-but-poisoned；
- success retention / admission precision。

---

## RQ4：TraceEvo 各组成模块是否对其设计能力必要，其工程开销如何？

对应证据：

- w/o ProvenanceTier；
- w/o CrossGraph；
- w/o Integrity；
- w/o Ledger；
- local processing overhead；
- storage overhead。

注意：

`w/o Ledger` 对当前 ParserBoundary 指标没有下降，这是负消融结果，必须如实报告。

---

# 7. 论文总体结构冻结

推荐正文结构：

```text
摘要
1 引言
2 相关工作
3 问题定义与总体框架
4 TraceEvo 方法
5 实验设计
6 实验结果与分析
7 讨论与局限
8 结论
```

如果期刊版面较紧，可把 5 和 6 合并成“实验与分析”。

---

# 8. 第 1 节：引言

## 8.1 第一段：场景

从医学多智能体协同推理切入：

- 中央智能体负责任务分解与编排；
- 专家智能体负责影像、病理、检验等分析；
- 外部记录按需检索；
- 推理涉及多个主体、模型调用和中间证据。

若要对接“边缘智能协同计算”专题：

强调医学多智能体可能运行在异构边云环境，执行链比单模型更长，因此：

```text
协作效率
执行安全
可追溯性
经验污染
```

问题更突出。

不要说本文实现了新的隐私机制。

---

## 8.2 第二段：Gap 1 —— reasoning graph 不等于 execution provenance

GoS 能表达：

```text
Hypothesis / Evidence / support / refute
```

但不能完整回答：

```text
哪个 agent 产生？
经过哪个 LLM call？
读取哪个 record？
是否被 parser 丢弃？
事件链有没有被修改？
```

这是 Contribution 1 的入口。

---

## 8.3 第三段：Gap 2 —— 轨迹有记录不等于轨迹可信

提出：

- fabricated / unsupported Evidence；
- misbinding；
- invalid dispatch；
- trace tampering；

说明只存 log 不等于具有审计语义。

这是 Contribution 2 的入口。

---

## 8.4 第四段：Gap 3 —— outcome correct 不等于经验可复用

用概念例子：

```text
答案正确，但过程包含 unsupported evidence
```

同样：

```text
过程无告警，但最终答案错误
```

所以：

```text
OutcomeOnly 不够
AuditOnly 也不够
```

这是 Contribution 3 的入口。

---

## 8.5 第五段：贡献

只列 §5 的三个贡献。

不要把：

- Memory retrieval；
- Capability Profile；
- ATGG；

列入贡献。

---

# 9. 第 2 节：相关工作

建议四小节。

## 2.1 医学 LLM 与多智能体协同推理

重点：

- multi-agent medical reasoning；
- expert-role collaboration；
- dynamic auxiliary examination / retrieval；
- GoS 作为直接基础工作。

必须明确：

> TraceEvo 扩展的是 GoS 的执行可信性层，而不是重新提出其医学 causal graph。

---

## 2.2 Agent provenance、可观测性与审计

讨论：

- execution trace；
- tool-call provenance；
- event graph；
- audit log；
- integrity protection。

指出缺口：

> 现有日志通常缺乏与 reasoning state 的双向绑定，难以从执行异常回到推理结论路径。

---

## 2.3 Agent memory 与经验学习

讨论：

- episodic memory；
- reflection；
- trajectory reuse；
- agent post-training / feedback learning。

指出本文区别：

> TraceEvo 不把历史轨迹直接写入长期记忆，而先做 outcome + audit 的可信准入。

---

## 2.4 可信协同与异常防护

讨论：

- hallucination / grounding；
- tool/capability misuse；
- trace tampering；
- safety gate。

不要把 P0–P3 写成已有标准；明确是本文 operational taxonomy。

---

# 10. 第 3 节：问题定义与总体框架

## 3.1 GoS 基础流程

复现源码实际流程：

```text
Initialization
→ FindFocus
→ Plan
→ ReAct experts
→ UpdateGraph
→ StateConversion
→ CheckReport
→ Report
```

只介绍必要内容。

---

## 3.2 两类图的定义

### Reasoning/Causal Graph

节点：

```text
Hypothesis
Evidence
```

关系：

```text
support
refute
refine / related state relations
```

职责：

> 表达医学推理状态。

### Collaboration Provenance Graph

节点/事件：

```text
PLAN
AGENT_CALL
TOOL_CALL
EVIDENCE_READ
ANALYSIS
GRAPH_UPDATE
BACKTRACK
DRILL_DOWN
FINAL_REPORT
```

职责：

> 表达执行来源和依赖。

---

## 3.3 Cross-Graph Binding

定义三类 binding：

```text
event → causal node
record → evidence node
graph delta → execution event
```

强调：

> structural provenance ≠ counterfactual causal influence。

本文不声称 execution dependency 是 LLM 决策的严格因果 attribution。

---

## 3.4 Threat / Fault Model

最终正文只定义 headline：

```text
A1 Unsupported/Fabricated Evidence
A2 Evidence Misbinding/Tampering
A3 Invalid Capability Invocation
A5 Trace Integrity Tampering
```

A4：

```text
Invocation/Parser Boundary
```

放 case study。

A0：

不进入 final headline。

---

## 3.5 Trustworthy Experience 定义

经验是否可复用取决于两个维度：

```text
Outcome
Process Trustworthiness
```

形成：

| Outcome | Audit | Experience |
|---|---|---|
| correct | trustworthy | Validated Success |
| incorrect + localized failure | auditable failure | Validated Failure |
| ambiguous / soft evidence | unresolved | Manual Review |
| unusable | incomplete/unsafe | Reject |

---

# 11. 第 4 节：TraceEvo 方法

## 4.1 总体架构

对应 Figure 1。

流程：

```text
GoS reasoning
      ↓
Invocation Ledger + Provenance Events
      ↓
Cross-Graph Binding
      ↓
Evidence / Integrity / Execution Audit
      ↓
Outcome Verification
      ↓
Experience Gate
      ↓
Success / Failure / Review / Reject
```

---

## 4.2 Model Invocation Ledger 与事件语义

说明两层观测：

```text
Invocation Ledger
    每次真实 LLM 请求/响应

Semantic Provenance Event
    已提交进 GoS 状态的语义操作
```

关键价值：

> parser 丢弃的调用仍可观测。

---

## 4.3 Evidence Provenance Tier

### P0 Direct Grounded

输入事实 / 直接 record binding。

### P1 Soft Grounded

存在候选记录，但仅软匹配。

### P2 Derived

合法推导、absence claim、expert synthesis、domain reasoning。

重要：

> P2 可追溯，但不是外部事实证据。

### P3 Unsupported Assertion

事实性 Evidence：

- 无输入来源；
- 无检索来源；
- 不属于合法 derived；
- 被用于 support/refute 或 final reasoning。

---

## 4.4 Integrity 与执行审计

介绍：

- hash chain；
- status/reason；
- invalid dispatch；
- parent-event DAG；
- incomplete task；
- record hash。

---

## 4.5 异常定位

流程：

```text
detected anomaly
→ earliest observable event
→ execution dependency traversal
→ cross-graph binding
→ affected causal nodes
→ conclusion path
```

定义：

```text
latent injection root
observable failure seam
```

---

## 4.6 Outcome Verification

强调 post-hoc：

```text
推理和 seal 完成
→ verifier 才读取 gold
```

禁止 gold 出现在：

- Planner；
- Memory payload；
- retrieval key；
- source trace。

---

## 4.7 Experience Gate

Frozen Gate：

### Success

需要：

```text
verified_success
integrity_ok
invocation_complete
semantic_trace_complete
no critical anomaly
no unresolved decision-path P3
P1-on-decision-path 按冻结规则进入 review
```

### Failure

有可定位异常和可重建 trace。

### Manual Review

例如：

- uncertain outcome；
- P1 on decision path；
- unresolved semantic risk。

---

# 12. 第 5 节：实验设计

## 5.1 Dataset / Split

必须披露完整研究流程，避免只展示 final 58。

建议表：

| Cases | Role |
|---|---|
| 0–9 | Early development / audit rules |
| 10–29 | Blind migration / historical experience |
| 30–39 | Initial memory build |
| 40–49 | Historical behavior probe |
| 50–59 | Experience expansion / profile evaluation |
| 60–69 | Gold-blind trigger-density cohort |
| 70–79 | Protocol validation |
| 80–137 | Final held-out |

同时说明：

> 最终 headline 指标来自 80–137，早期数据用于开发和形成冻结规则。

---

## 5.2 Backbone

冻结：

```text
deepseek-v4-flash
```

论文不要用多个 backbone 的措辞。

单 backbone 是 limitation。

---

## 5.3 Baselines

### Audit / anomaly

```text
Full TraceEvo
w/o ProvenanceTier
w/o CrossGraph
w/o Integrity
w/o Ledger
```

### Experience Gate

```text
Naive
OutcomeOnly
AuditOnly
IntegrityOnly
TraceEvo
```

---

## 5.4 Metrics

按 RQ 分组：

### RQ1

- seal verification；
- chain verification；
- graph replay；
- trace completeness；
- Core ON/OFF equivalence；
- overhead。

### RQ2

- clean trace alert；
- clean evidence alert；
- per-class Precision/Recall/F1；
- Macro-F1；
- Observable-root Top-1；
- Latent-root exact；
- secondary anomaly count。

### RQ3

- incorrect-clean admission；
- correct-but-poisoned admission；
- success retention；
- admission precision；
- failure capture；
- manual review。

### RQ4

- module capability drop；
- local overhead；
- storage。

---

## 5.5 Statistics

冻结：

```text
diagnostic metrics:
case-level bootstrap, 10,000, seed=20260920

clean alert:
Wilson 95% CI

controlled anomaly:
case-cluster bootstrap

Gate proportions:
Wilson 95% CI
```

---

# 13. 第 6 节：实验结果——表格冻结

# Table 1：Final Held-out Natural Performance and Audit

主文建议不要把所有内部调试字段塞进去。

冻结为：

| Metric | Final held-out (N=58) | Protocol-clean sensitivity (N=52) |
|---|---:|---:|
| Match | 0.086 [0.017, 0.172] | 0.096 [0.019, 0.192] |
| Relevant | 0.379 [0.259, 0.500] | 0.385 [0.250, 0.519] |
| verified_success | 7 | 7 |
| verified_incorrect | 31 | 28 |
| uncertain | 20 | 17 |
| Clean Trace Alert | 16/58 = 0.276 | 14/52 = 0.269 |
| Clean Evidence Alert | 12/58 = 0.207 | 11/52 = 0.212 |
| P0 nodes | 317 | 289 |
| P1 nodes | 4 | 4 |
| P2 nodes | 438 | 390 |
| P3 nodes | 16 | 14 |
| P3 used | 14 | 12 |
| Seal / replay | 58/58 | 52/52 |

Protocol-clean sensitivity 排除：

```text
80, 81, 82
    pre-pass exposure

104, 107, 121
    non-preregistered model-output retry
```

### Table 1 的论文解释

不要把 Match 低解释成 TraceEvo 的失败或提升。

写：

> TraceEvo 在本实验中以旁路审计方式运行，不改变 GoS 的基础诊断行为；诊断指标用于描述 underlying reasoning pipeline，而非作为 TraceEvo 的性能增益指标。

---

# Table 2：Controlled Anomaly Detection

冻结最终值：

| Class | N | Precision | Recall | F1 |
|---|---:|---:|---:|---:|
| A1 Unsupported Evidence | 58 | 0.826 | 0.983 | 0.898 |
| A2 Misbinding/Tampering | 54 | 1.000 | 0.889 | 0.941 |
| A3 Invalid Capability | 58 | 1.000 | 1.000 | 1.000 |
| A5 Trace Tampering | 232 | 1.000 | 1.000 | 1.000 |
| **Macro** | 402 | 0.957 | 0.968 | **0.960** |
| **Micro** | 402 | 0.971 | 0.983 | **0.977** |

Macro-F1：

```text
95% CI [0.943, 0.975]
```

### 必须附一句

> A1 precision 较低主要来自 12 条自然 trace 本身触发真实 P3/A1 finding；这些发现按照预注册口径保留在 clean false-positive denominator 中。

---

# Table 3：Traceability and Root Localization

冻结：

| Metric | Result |
|---|---:|
| Observable-root Top-1 | **393/402 = 0.978** |
| Latent-root exact | **277/286 = 0.969** |
| Seal verified | 58/58 |
| Trace complete | 58/58 |
| Graph replay | 58/58 |
| Original artifacts unchanged after mutation | Yes |

正文解释：

> delete / truncate 类异常的 latent event 已不存在，因此主指标采用 observable failure seam。

---

# Table 4：Outcome-Audit Experience Gate

这是论文最核心的表。

## 4A. 两种结构性反例

| Gate | Incorrect-clean admitted | Correct-but-poisoned admitted |
|---|---:|---:|
| Naive | 22/22 | 19/19 |
| OutcomeOnly | **0/22** | **19/19** |
| AuditOnly | **22/22** | 2/19 |
| IntegrityOnly | 22/22 | 0/19 |
| **TraceEvo** | **0/22** | **0/19** |

正文必须抓住：

```text
OutcomeOnly:
知道答案对不对，不知道过程是否被污染。

AuditOnly:
知道过程有没有明显异常，不知道答案本身是不是错的。

TraceEvo:
必须同时满足 outcome + process。
```

## 4B. Experience admission

建议主文另给：

| Rule | Success retention | Admission precision | Failure capture |
|---|---:|---:|---:|
| Naive | 5/5 | 5/77 = 0.065 | 0/16 |
| OutcomeOnly | 5/5 | 5/26 = 0.192 | 0/2 |
| AuditOnly | 5/5 | 5/44 = 0.114 | N/A |
| IntegrityOnly | 5/5 | 5/58 = 0.086 | 0/16 |
| **TraceEvo** | **5/5** | **5/5 = 1.000** | **16/16** |

注意：

Admission precision 的 1.000 基于 5 个 positive，CI 很宽 `[0.566,1.000]`。正文不要用“完美”这种语言。

---

# Table 5：Ablation

冻结：

| Arm | A1 F1 | A2 F1 | A5 F1 | Evidence Trace | RootLoc | Parser Boundary |
|---|---:|---:|---:|---:|---:|---:|
| Full | 0.898 | 0.941 | 1.000 | 0.985 | 0.978 | 1.000 |
| w/o ProvenanceTier | **0.000** | 0.941 | 1.000 | 0.861 | 0.841 | 1.000 |
| w/o CrossGraph | 0.898 | **0.699** | 1.000 | 0.944 | **0.930** | 1.000 |
| w/o Integrity | 0.898 | 0.941 | **0.000** | 0.480 | 0.401 | 1.000 |
| w/o Ledger | N/S | N/S | N/S | N/S | N/S | **1.000** |

### 冻结解释

- ProvenanceTier 对 A1 是必要组件；
- CrossGraph 对 A2 / root localization 有明显贡献；
- Integrity 对 A5 是必要组件；
- Ledger 在当前 ParserBoundary 指标中没有显示出消融下降。

最后一条不能改写成“Ledger 实验证明必要”。

Ledger 的必要性主要来自：

> 对未提交 model call 的 observability 设计需求与 S3A/A4 case study。

---

# Table 6 / Appendix A：Protocol Deviation Sensitivity

建议放附录或实验末尾的小表。

已知偏离：

| Type | Cases | Treatment |
|---|---|---|
| pre-pass exposure | 80–82 | final full block 保留；另做 exclusion sensitivity |
| non-preregistered retry | 104,107,121 | full block 保留；另做 exclusion sensitivity |
| reporting driver corrections | analysis stage | raw Gate decisions 保留，重新从 artifact 汇总 |

Sensitivity：

```text
N = 52
Macro anomaly F1 = 0.957
Observable-root = 0.975
verified_success = 7
incorrect-clean = 20
correct-but-poisoned twins = 19
```

正文建议一句：

> 排除全部已知 protocol-affected cases 后，核心异常检测、根因定位与 Experience Gate 结论保持一致。

---

# Table 7 / Appendix B：Overhead

主文如版面允许放 Table 5b，否则放附录。

| Quantity | Median | IQR | p95 |
|---|---:|---:|---:|
| Event logging | 0.248 ms | 0.187–0.345 | 0.446 |
| Ledger | 0.194 ms | 0.145–0.281 | 0.319 |
| Graph delta | 0.353 ms | 0.213–0.549 | 0.765 |
| Audit | 9.700 ms | 6.261–16.428 | 22.750 |
| Gate | 0.012 ms | 0.011–0.014 | 0.016 |
| **Local total** | **10.450 ms** | 6.907–17.690 | 23.922 |
| Storage | **0.848 MiB/case** | 0.667–0.995 | 1.036 |

## Token 结果冻结为“不进入主文正式 overhead 结论”

S5B transport log 报告：

```text
11,109,269 tokens
2295 usage-bearing calls
```

但 Invocation Ledger 为：

```text
2160 model calls
```

两套 instrumentation 未对齐。

因此：

> 不把 token total 放入主实验表。

可在 limitation 写：

> Transport-layer token logs were available, but their call count did not reconcile with the invocation ledger; token usage was therefore excluded from the formal overhead comparison.

---

# 14. Figures 冻结

## Figure 1：TraceEvo 总体框架

使用 / 重绘：

```text
S5B_final_heldout/figures/figure1_core_v1_framework.png
```

图中只保留 Core-v1：

```text
GoS
→ Invocation/Provenance
→ Dual-Graph Binding
→ Audit
→ Outcome Verification
→ Experience Gate
```

不要画：

- Capability Profile；
- ATGG；
- online Memory injection。

---

## Figure 2：双图耦合示例

对应：

```text
figure2_dual_graph_coupling.png
```

左：

```text
Execution DAG
PLAN → Agent → Read → Analysis → GraphUpdate
```

右：

```text
Causal Graph
Evidence → Hypothesis
```

中间画：

```text
binding IDs
```

图注必须说明：

> execution dependency 不代表 counterfactual causal attribution。

---

## Figure 3：异常检测与根因追溯

对应：

```text
figure3_anomaly_root_propagation.png
```

展示：

```text
Injected / natural anomaly
→ observable event
→ dependency path
→ affected reasoning node
→ report
```

latent root 与 observable seam 用不同形状。

---

## Figure 4：Experience Gate 双反例

对应：

```text
figure4_experience_gate.png
```

建议不要做成普通柱状图。

画成二维逻辑：

```text
Outcome correct?
Process trustworthy?

       trustworthy   polluted
correct   ACCEPT       BLOCK
wrong     BLOCK        FAILURE/REVIEW
```

旁边放 held-out 数字：

```text
OutcomeOnly: 19/19 poisoned accepted
AuditOnly:   22/22 incorrect-clean accepted
TraceEvo:     0/19 and 0/22
```

---

# 15. 第 6 节结果叙述顺序冻结

不要按照开发阶段 S1→S5 写论文 Results。

按 RQ 写。

## 6.1 RQ1：Traceability

先证明：

```text
58/58 seal
58/58 replay
Core ON/OFF non-invasive
```

再给 provenance tier 自然分布。

---

## 6.2 RQ2：Anomaly Detection

先 Table 2。

然后单独讨论：

### A1

不是 1.0。

强调：

```text
precision 0.826
```

是最值得讨论的类别，因为自然 unsupported evidence 与 injected A1 会重叠。

### A2

重点解释 misbinding 与 record hash。

### A3

强调 planner dispatch 是实际系统中出现过的自然缺陷，而非完全脱离系统的 toy error。

### A5

强调 integrity 链对 modification/delete/reorder/truncate。

---

## 6.3 RQ2b：Root Localization

Table 3。

重点：

```text
observable root vs latent root
```

这是方法严谨性亮点。

---

## 6.4 RQ3：Experience Gate

Table 4。

这是整篇论文叙事高潮。

先 incorrect-clean，再 correct-but-poisoned。

不要先给大而全的 Gate 指标。

---

## 6.5 RQ4：Ablation + Overhead

Ablation 解释每个模块“负责什么”。

最后给 local overhead。

---

# 16. 第 7 节：Discussion 冻结

必须包含四个负结果。

## 7.1 Memory 没有证明提高诊断

S3B：

```text
M0 NoMemory
```

的 Match / Relevant 反而最高。

所以：

> 本文没有把可信经验“被筛选出来”进一步等价为“把它注入 Planner 一定有帮助”。

---

## 7.2 Role-specific Profile 没有得到支持

S4A：

- role-specific profile 没有显示出相对 global baseline 的预测增益；
- interval 重叠；
- execution abnormal target 缺少正例。

所以：

> 不进行专家历史信誉排序。

---

## 7.3 ATGG 没有自然 efficacy 证据

S4B：

```text
case 60–69 P3 = 0
```

导致 guard 无法在该 cohort 上做定量 efficacy。

即使 S5A/S5B 后面自然 P3 重新出现，也不能倒推 S4B 已经验证 efficacy。

---

## 7.4 “自进化”的边界

建议论文最后定义：

> TraceEvo 本文实现的是 **audit-gated experience consolidation**，即为后续持续演进提供可信数据基础。

真正的：

```text
memory-driven improvement
policy adaptation
parameter post-training
```

属于 future work。

---

# 17. Limitations 冻结

正文至少写 6 点。

## L1 单数据集 / 单 backbone

```text
DiagnosisArena
deepseek-v4-flash
```

不能推断到其他模型/医院/语言。

## L2 Controlled fault model

Macro-F1 只针对本文 injector fault model。

## L3 自然 clean alert 并非纯人工 gold

自然 A1 finding 可能是真问题，也可能是 taxonomy/grounding 边界导致的 alert。

## L4 Outcome Judge

verified_success 依赖冻结的 judge protocol。

存在较多：

```text
score=1 / uncertain
```

说明 outcome verification 本身有边界。

## L5 Final protocol deviations

必须透明披露：

```text
case 80–82 pre-pass exposure
case 104/107/121 non-preregistered retries
post-freeze reporting-driver corrections
```

并给 52-case sensitivity。

## L6 没有证明在线演进收益

不声称：

- Memory improves diagnosis；
- role adaptation；
- ATGG efficacy；
- continual self-evolution gain。

---

# 18. 两处必须在写论文前修正文档口径

这不是改实验，只是结果文档一致性修正。

## 18.1 `final_limitations.md` 的 P1 描述错误

该文件写：

> P1 remains absent

但最终自然表实际为：

```text
P1 nodes = 4
P1 used = 3
```

论文以最终 Table 1 为准。

---

## 18.2 Token 文档冲突

一处写：

```text
TOKEN_UNAVAILABLE
```

另一处写：

```text
REPORTED_BY_API
11,109,269
```

论文统一处理：

> token 不进入正式 overhead claim，因为 usage-bearing call count 与 Invocation Ledger call count 不一致。

---

# 19. Protocol deviation 写法冻结

推荐 Methods / Limitations 中用中性表述。

不要写：

> “最终 held-out 完全严格一次性运行，没有任何偏离。”

写：

> Core-v1 的规则文件在最终评测中保持冻结。最终 58-case operational block 中，3 个病例曾在废弃 pre-pass 中执行，另有 3 个病例因模型生成非法专家名称而进行了非预注册 retry。我们保留全部记录，并额外报告排除这 6 个病例后的 52-case protocol-clean sensitivity analysis；核心结论保持稳定。

重要：

不要称这些偏离：

```text
无影响
```

而应称：

```text
sensitivity analysis did not materially change the reported core metrics
```

---

# 20. 摘要里允许写的数字

建议最多 4 组。

可写：

```text
402 controlled injections
Macro-F1 0.960 [0.943, 0.975]

Observable-root Top-1 0.978

OutcomeOnly:
19/19 correct-but-poisoned admitted
TraceEvo:
0/19

AuditOnly:
22/22 incorrect-clean admitted
TraceEvo:
0/22
```

如果摘要空间还有：

```text
local overhead median 10.45 ms/case
```

不写：

```text
Match 0.086
```

不是隐瞒，而是它不是方法贡献指标；正文 Table 1 必须完整披露。

---

# 21. Claim → Evidence 映射冻结

| Claim | Final evidence | 写作等级 |
|---|---|---|
| Dual-graph traceability | 58/58 seal/replay + bindings | Abstract / Conclusion |
| Controlled anomaly detection | 402 injections, Macro-F1 0.960 | Abstract / Conclusion |
| Observable root localization | 393/402 = 0.978 | Abstract / Conclusion |
| OutcomeOnly blind spot | 19/19 poisoned accepted | Abstract / Results |
| AuditOnly blind spot | 22/22 incorrect-clean accepted | Abstract / Results |
| Joint Gate blocks both | 0/19 + 0/22 | Abstract / Conclusion |
| Natural P3 exists | 16 nodes, 14 used | Results / Discussion |
| Experience partition | 5 eligible / 16 failure / 37 review | Results |
| Memory improves diagnosis | unsupported | NOT CLAIMED |
| Role routing useful | unsupported | NOT CLAIMED |
| ATGG effective | unsupported | NOT CLAIMED |
| Online self-evolution improves performance | unsupported | NOT CLAIMED |

---

# 22. Artifact → 论文位置映射

## Core 结果

```text
S5B_final_heldout/tables/table1_heldout_natural.md
→ Table 1

table2_controlled_anomaly.md
→ Table 2

table3_root_traceability.md
→ Table 3

table4_experience_gate.md
→ Table 4

table5a_ablation.md
→ Table 5A

table5b_overhead.md
→ Table 5B / Appendix
```

## Figures

```text
figures/figure1_core_v1_framework.png
figures/figure2_dual_graph_coupling.png
figures/figure3_anomaly_root_propagation.png
figures/figure4_experience_gate.png
```

## Limitations / reproducibility

```text
failure_cases.md
run_audit.json
driver_drift_report.json
discarded_pass.json
final_limitations.md
reproduce_final.sh
```

---

# 23. 推荐篇幅分配

按中文期刊约 10–12 页正文设计：

| Section | 比例 |
|---|---:|
| 引言 | 12% |
| 相关工作 | 10% |
| 问题定义 | 10% |
| 方法 | 28% |
| 实验设计 | 12% |
| 实验结果 | 20% |
| Discussion + Limitations | 6% |
| 结论 | 2% |

方法和实验必须占主体。

不要用大量篇幅讲 S0–S5 的开发历程。

---

# 24. 写作中禁止使用的表述

冻结禁止：

- “TraceEvo 显著提高了医学诊断准确率”
- “TraceEvo 实现了持续自进化”
- “可信记忆提升了诊断性能”
- “不同专家具有显著不同的可信度”
- “ATGG 已证明能够改善自然医学任务”
- “异常检测准确率达到 96%”

最后一句应改为：

> “在本文定义的 controlled fault model 上取得 Macro-F1 0.960。”

---

# 25. 推荐可使用的表述

推荐：

- “可重放的全过程 provenance”
- “双图耦合审计”
- “来源感知的 Evidence audit”
- “observable failure seam”
- “outcome–audit joint eligibility”
- “trustworthy experience consolidation”
- “为后续持续演进提供可信经验基础”
- “在受控 fault model 下验证”
- “未观察到……的充分证据”
- “结果不支持采用 role-specific routing”

---

# 26. 投稿版本的核心故事

完整故事压缩成六步：

```text
① GoS 有 reasoning graph，但缺少完整 execution provenance。

② TraceEvo 建第二张 provenance graph，并通过 cross-graph binding
   把“执行链”与“推理结论”连接起来。

③ 有 provenance 后，系统才能区分 grounded / derived / unsupported Evidence，
   检测 tampering、misbinding、invalid invocation，并追溯 observable root。

④ 但 audit clean 不代表答案正确；
   outcome correct 也不代表过程可信。

⑤ 因此 TraceEvo 将 Outcome Verification 与 Audit 联合成 Experience Gate。

⑥ Held-out 的两类反例同时证明：
   OutcomeOnly 与 AuditOnly 均有结构性盲点，
   联合 Gate 才能构造可追溯的可信经验集合。
```

这就是第一篇论文的完整闭环。

---

# 27. 下一步写作顺序

现在不再做科研实施阶段 review。

建议按以下顺序直接落稿：

```text
P1. 先写 Section 4 方法
P2. 写 Section 5 实验设计
P3. 写 Section 6 Results
P4. 回写 Introduction
P5. 写 Related Work
P6. 写 Discussion / Limitations
P7. 最后写 Abstract / Conclusion
```

理由：

现在方法和结果已经完全冻结，先写最确定的部分，能够防止引言先把贡献吹得超过证据。

---

# 28. 本版本之后的冻结规则

**允许：**

- 重新绘图；
- 调整论文表格排版；
- 从现有 artifact 重算同口径 CI；
- 检查数字；
- 改文字；
- 补文献。

**不允许：**

- 新跑病例；
- 改 Gate；
- 改 P0–P3；
- 调 OutcomeVerifier；
- 更换 backbone；
- 重新启用 Memory；
- 重新启用 ATGG；
- role routing；
- 用 held-out 结果设计新规则后再重跑 held-out。

---

# 29. 下一份写作产物建议

下一步直接生成：

**《TraceEvo 论文初稿 v1 —— 方法与实验》**

优先完成：

```text
3 问题定义与总体框架
4 TraceEvo 方法
5 实验设计
6.1–6.5 实验结果
```

写完后再补引言和相关工作。

