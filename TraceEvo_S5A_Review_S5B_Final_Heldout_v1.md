# TraceEvo：S5A评审结论与S5B最终Held-out实施指令 v1.0
## ——Core-v1 最终冻结、58例一次性Held-out评测与论文证据封存

## 0. 评审结论

**结论：接受 `GO_S5B_FINAL_HELDOUT`。允许进入 S5B。**

S5A 已经完成进入最终 Held-out 的全部协议验证条件：

- case 70–79：10/10 正常执行并完成 seal；
- 事件、Invocation Ledger、GraphDelta 与图重放全部通过；
- gold leakage = 0；
- Core ON/OFF 非侵入性通过；
- controlled anomaly protocol 在 artifact 副本上可重复执行；
- final table 所需字段均可自动生成；
- case 80–137 尚未读取；
- Core-v1 未因 validation 结果修改。

因此，从现在起：

> **算法、审计规则、OutcomeVerifier、Experience Gate、数据处理协议全部冻结。**

S5B 是一次性最终实验。其目的不是继续开发，而是回答：

1. Core-v1 在 58 个完全未见病例上的自然审计行为如何？
2. 双图审计对受控异常的检测、可观测根因定位是否能够泛化？
3. Outcome + Audit 联合 Gate 在最终 Held-out 上能获得什么证据？
4. 哪些论文结论得到最终 Held-out 支持，哪些仍只能作为 development evidence 或 limitation？

**S5B 完成后停止代码开发，进入论文结果整理。**

---

# 1. S5A 结果的正式解释

## 1.1 S5A 通过的是“协议”，不是诊断性能

case 70–79：

```text
verified_success = 0
verified_incorrect = 9
uncertain = 1
```

这不影响 S5A protocol validation 的通过，因为 S5A 的门槛是：

- seal 是否可靠；
- audit 是否稳定；
- Gate 是否按冻结规则运行；
- anomaly protocol 是否可复现；
- Core 是否非侵入。

但它对 S5B 有一个重要约束：

> **最终论文不能预设 Held-out 中一定会出现足够的 `verified_success`。**

S5B 必须允许以下真实结果存在：

```text
HELDOUT_POSITIVE_CLASS_SPARSE
或
HELDOUT_POSITIVE_CLASS_ABSENT
```

不得因此修改 Judge、Gate 或继续寻找病例。

## 1.2 S5A 中 P3 自然重现，但不重新打开 ATGG

case-76 出现 2 个真实 P3-used 节点，只能作为：

```text
natural audit observation
```

不能作为重新启用 ATGG 的理由。

ATGG 已在 S4B 被降为 exploratory module。

S5B：

```text
ATGG = OFF
```

保持不变。

---

# 2. S5B 最终研究声明在运行前冻结

在第一次读取 case 80 之前，生成：

```text
FINAL_CLAIM_MANIFEST.md
```

并固定以下声明边界。

## C1 —— 最终主张

**TraceEvo 能够在 GoS 医学多智能体推理过程中建立细粒度、可重放的执行 provenance，并将执行来源与因果推理图关联。**

Held-out 证据来源：
- replay；
- cross-graph binding；
- source tier；
- integrity；
- natural trace。

## C2 —— 最终主张

**TraceEvo 能够利用 provenance、证据来源和完整性信息检测受控协作异常，并定位可观测故障起点。**

Held-out 证据来源：
- A1/A2/A3/A5 controlled injection；
- clean alert；
- Observable-root localization。

## C3 —— 条件性主张

**联合 Outcome Verification 与 Audit 的 Gate 能避免单一信号造成的不安全经验准入。**

Held-out 是否支持，取决于 80–137 中是否存在：
- verified-success；
- incorrect-clean；
- correct-but-poisoned twins。

若 Held-out 没有足够 positive success：

```text
C3-positive branch = development-supported only
```

必须如实标注。

## C4 —— 可以主张

**TraceEvo 可以将审计结果转化为可追溯的 Success / Failure / Manual Review / Reject 经验分区。**

这是一种：

```text
Trustworthy Experience Consolidation
```

而不是“已证明持续提高诊断性能”的自进化。

## 不主张

S5B 无论结果如何都不主张：

- Success Memory 提高诊断准确率；
- role-specific routing 有效；
- ATGG 提高自然任务效果；
- TraceEvo 通过在线自进化持续提升医学诊断性能。

---

# 3. 最终数据集

S5B 唯一允许使用：

```text
case 80–137
```

共：

```text
58 cases
```

S5B 期间不得重新读取未封存的开发病例来调整任何规则。

历史数据只允许用于：
- 已冻结规则本身；
- development-vs-heldout 对照表；
- 已有论文结果复核。

---

# 4. S5B-A：Final Freeze

读取 case 80 前生成：

```text
Final_Core_v1_freeze_manifest.json
```

必须记录：

```text
GoS commit
TraceEvo commit
environment hash
requirements hash
dataset hash / case index hash

backbone
temperature
max_steps
delta
eta
retrieval budget

P0-P3 rule hash
absence rule hash
audit policy hash
integrity policy hash
OutcomeVerifier prompt hash
OutcomeVerifier configuration
Experience Gate hash

Memory injection = OFF
Failure Memory activation = OFF
Role adaptation = OFF
ATGG = OFF

controlled injection specification hash
evaluation script hash
table generator hash
```

再生成：

```text
FINAL_DATA_ACCESS_LOG.json
```

首次访问 case 80 的时间必须晚于 freeze manifest。

---

# 5. 最终运行原则：One-pass Held-out

## 5.1 每个病例严格顺序

```text
1. 读取当前 case 任务输入
2. Core-v1 正常执行
3. events / ledger / graph / deltas / raw outputs 完整落盘
4. seal
5. seal verification
6. natural audit
7. graph replay verification
8. 此时才允许 OutcomeVerifier 读取 gold
9. frozen Judge × 3
10. Experience Gate
11. candidate 保存
12. 不把该 candidate 注入后续病例
```

case 80 的结果不得影响 case 81 的执行策略。

## 5.2 允许重跑的唯一情况

只有基础设施故障允许 exact rerun，例如：

- 网络连接中断；
- API 5xx；
- 机器进程意外退出；
- 文件系统故障。

要求：

```text
same frozen code
same frozen config
rerun_reason recorded
attempt history preserved
```

不得删除第一次失败记录。

## 5.3 如果发现算法/协议 Bug

例如：

- P0-P3 规则实现错误；
- Gate 逻辑错误；
- audit 统计口径错误；
- gold 提前泄漏；
- final output 因 Core 代码缺陷无效。

则：

```text
FINAL_HELDOUT_INVALIDATED
```

立即停止。

**不得修代码后继续把 case 80–137 当“未见最终集”重新跑一遍。**

此时必须回评审决定：
- 是否需要新数据；
- 是否把本轮降级为 additional development evidence。

---

# 6. S5B-B：自然任务结果

对 58 个病例首先只报告原始自然运行，不混 controlled injection。

## 6.1 标准诊断指标

必须同时保留两个不同层次。

### GoS-style diagnostic evaluation

沿用当前 frozen Match / Relevant evaluator：

```text
Match
Relevant
```

报告：
- mean；
- case-level distribution；
- 95% bootstrap CI。

### Experience Outcome categories

单独报告：

```text
verified_success
verified_incorrect
uncertain
```

不得把：

```text
verified_incorrect
```

直接等价为 GoS `Match=0`。

二者是不同用途的指标。

## 6.2 自然 provenance / audit 统计

至少：

```text
event count
LLM call count
accepted call count
discarded call count

Evidence P0
Evidence P1
Evidence P2
Evidence P3

P3-used-in-reasoning
clean trace alerts
clean evidence alerts

seal failures
graph replay failures
ledger binding failures
critical anomalies

experience:
eligible
failure_experience
manual_review
reject
```

## 6.3 Clean Alert

最终必须报告：

```text
Clean Trace Alert Rate
Clean Evidence Alert Rate
```

并给 Wilson 95% CI。

自然 trace 中的真实审计发现仍算 alert。

不要因为它可能是真问题就把它从 clean alert denominator 删除。

---

# 7. S5B-C：最终受控异常实验

只有 58 条自然 trace 全部完成 seal 后才能开始。

必须复制 artifact：

```text
heldout_original/
→
heldout_mutations/
```

原始 seal 永不修改。

## 7.1 Headline anomaly types

冻结为：

```text
A1 Unsupported / Fabricated Evidence
A2 Evidence Misbinding / Record Tampering
A3 Invalid Capability Invocation
A5 Trace Integrity Tampering
```

A4 只作为：

```text
Invocation / Parser Boundary Case Study
```

不进入 headline semantic propagation F1。

A0 不进入 headline。

## 7.2 注入次数

原则上每个适用 case 每类 1 次：

```text
A1: up to 58
A2: up to 58
A3: up to 58
A5: 按冻结的 tamper variants
```

若某类对某病例不适用：

```text
NOT_APPLICABLE
```

而不是强行构造。

必须报告每类真实 denominator。

---

# 8. 异常指标的最终统计口径

## 8.1 Primary Detection

每类分别：

```text
Precision
Recall
F1
```

然后：

```text
Macro average by anomaly class
Micro average
```

Headline 优先使用：

```text
Macro by class
```

避免样本较多类别主导结果。

## 8.2 置信区间

因为同一 case 会生成多个 mutation，不能把所有 mutation 当完全独立样本做普通 bootstrap。

使用：

```text
case-cluster bootstrap
```

以 case_id 为采样单元，95% CI。

如果本地实现复杂，可以同时给：

```text
per-class point estimate
Wilson CI for simple proportions
```

但不要做错误的 event-level independence 假设。

## 8.3 Secondary Findings

报告：

```text
secondary_anomaly_count
secondary_anomaly_types
```

不把注入后真实产生的二次异常机械计成 primary-class FP。

---

# 9. Root Localization 最终口径

统一两种 root：

## Latent Injection Root

实验系统知道的：

```text
真正被修改 / 删除 / 注入的对象
```

## Observable Failure Seam

审计系统从剩余 trace 中实际能看到的最早异常断点。

Headline：

```text
Observable-root Top-1 Accuracy
```

另报告：

```text
Latent-root exact accuracy
```

仅对 latent root 仍存在于 trace 的类别适用。

删除 / truncate 不得要求系统定位一个已经不存在的 event。

---

# 10. S5B-D：Experience Gate 最终 Held-out 实验

## 10.1 Natural Candidate

58 个自然病例各生成一个 candidate。

比较：

```text
Naive
OutcomeOnly
AuditOnly
IntegrityOnly
TraceEvo
```

## 10.2 Incorrect-clean

定义保持冻结。

统计：

```text
incorrect-clean candidates
AuditOnly admission
TraceEvo admission
```

这是 Held-out 中预计最有样本量的 Gate 负类证据。

## 10.3 Correct-but-poisoned

只有：

```text
verified_success
AND clean trace
AND eligible/reviewable according to frozen protocol
```

的病例才能构造。

对每个 heldout verified-success，构造冻结的 poison twins：

```text
PZ1
PZ2
PZ3
PZ4
```

如果某类型不适用，记录 NA。

## 10.4 如果 final success 很少

### 若 verified_success >= 5

允许报告 heldout Gate positive-class 指标及 CI。

### 若 verified_success = 1–4

报告：

```text
HELDOUT_POSITIVE_CLASS_SPARSE
```

只给计数和案例，不做强统计结论。

### 若 verified_success = 0

报告：

```text
HELDOUT_POSITIVE_CLASS_ABSENT
```

不得：
- 修改 Judge；
- 使用 score=1；
- 将 uncertain 改成 success；
- 从旧 development 数据混入 heldout denominator。

论文写法：

> correct-but-poisoned 的 positive-branch 结论由 development controlled experiment 支持；final heldout 未产生可用于独立复验的 positive class。

---

# 11. Gate 最终指标

有适用 denominator 时报告：

```text
Poison Admission Rate
Correct-but-Poisoned Admission Rate
Incorrect-Clean Admission Rate
Validated Success Retention
Admission Precision
Admission Recall
Manual Review Rate
Failure Capture Rate
```

所有比例报告：

```text
numerator / denominator
95% CI
```

不得只写 0.0 / 1.0。

---

# 12. S5B-E：Core Ablation

最终论文需要证明 Core-v1 不是一个不可解释整体。

只在 heldout artifact 副本上离线 disable 模块，不重新运行 LLM。

至少：

## Full TraceEvo

全部启用。

## w/o Evidence Provenance Tier

关闭：

```text
P0-P3 source-aware evidence audit
```

预期主要影响 A1。

## w/o Cross-Graph Binding

关闭：

```text
Evidence/Hypothesis ↔ execution event binding
```

评估：
- evidence trace；
- root localization；
- A1/A2。

## w/o Integrity Audit

评估 A5。

## w/o Invocation Ledger

只在：
- A4 parser boundary；
- uncommitted call observability

相关指标上测试。

不要把它放入与 A1/A2/A5 完全相同的总分里制造无意义平均。

---

# 13. Ablation 的正确输出

输出模块 × 能力矩阵：

```text
module_ablation_matrix.csv
```

例如：

```text
                    A1    A2    A5    EvidenceTrace    RootLoc    ParserBoundary
Full
w/o ProvenanceTier
w/o CrossGraph
w/o Integrity
w/o Ledger
```

优先证明：

> 某模块对它设计要解决的能力确实必要。

不要求每个模块让“总体 Macro-F1”都下降。

---

# 14. S5B-F：最终开销

分三类。

## Local TraceEvo

```text
event logging ms/case
ledger ms/case
graph delta ms/case
audit ms/case
gate ms/case
storage MB/case
```

报告：

```text
median
IQR
p95
```

## LLM/API

```text
number of model calls
wall-clock duration
```

网络 latency 只作为工程数据。

## Token

如果 API 没有可靠 usage：

```text
TOKEN_UNAVAILABLE
```

继续保持，不估算。

---

# 15. S5B-G：Final Case Study 选择规则预注册

避免最后挑最好看的案例。

在运行 final 前固定：

## Dual-Graph normal example

```text
case 80
```

无论结果如何均作为基础结构示例候选。

若图不可读，可仅因“技术绘图失败”改用 case 81，并记录原因。

## Natural P3 example

如果 heldout 存在自然 P3：

```text
选择 case_id 最小的含 P3-used 病例
```

不得按诊断结果挑。

## Controlled anomaly path

```text
case 80 的 A1
```

若 A1 不适用：

```text
按 case id 顺序选择第一个 applicable case
```

## Experience Gate case

如果有 verified-success：

```text
选择 case_id 最小的 heldout verified-success
```

并展示 clean vs poisoned Gate。

否则明确无 final positive case study。

---

# 16. 统计原则

## 诊断指标

case-level bootstrap：

```text
10,000 resamples
95% CI
seed = 20260920
```

## Clean alert

Wilson 95% CI。

## Anomaly

case-cluster bootstrap 或 per-class exact proportion CI。

## Gate

Wilson / exact binomial CI。

## 不做

- 不做无预注册的大量 p-value hunting；
- 不因为结果不好新增 subgroup；
- 不从 58 case 中反复搜索“显著子集”。

---

# 17. S5B 最终表格

自动生成至少 5 张主表。

## Table 1 — Held-out Natural Performance & Audit

```text
N
Match
Relevant
verified success / incorrect / uncertain
P0/P1/P2/P3
P3-used
clean trace alert
clean evidence alert
replay / seal integrity
```

## Table 2 — Controlled Anomaly Detection

按 A1/A2/A3/A5：

```text
N
Precision
Recall
F1
95% CI
```

## Table 3 — Root & Traceability

```text
Observable-root Top-1
Latent-root exact (applicable only)
Evidence-trace success
Integrity detection
```

## Table 4 — Experience Gate

```text
Naive
OutcomeOnly
AuditOnly
IntegrityOnly
TraceEvo
```

以及适用 denominator。

如果 positive class sparse / absent，表中明确标记，不隐藏。

## Table 5 — Ablation & Overhead

可以拆成 5a/5b：

```text
module necessity
local overhead
storage
```

---

# 18. 最终论文图

S5B 自动准备：

## Figure 1
TraceEvo Core-v1 总体框架：
- GoS reasoning graph
- provenance / ledger
- audit
- outcome verification
- experience gate

## Figure 2
双图耦合示例。

## Figure 3
受控异常 root → propagation / observable seam。

## Figure 4
Experience Gate：
```text
OutcomeOnly vs AuditOnly vs TraceEvo
```

如果 final positive class absent，Figure 4 可以使用：
```text
development verified counterexample
+
heldout negative-class confirmation
```
并在图注中清楚区分来源。

不再制作“自进化准确率提升曲线”作为主图。

---

# 19. Final Evidence Claim Matrix

必须输出：

```text
final_claim_evidence_matrix.md
```

格式至少：

| Claim | Development | Validation | Final Heldout | Status |
|---|---|---|---|---|
| Dual-graph traceability | yes | yes | ? | pending |
| Controlled anomaly detection | yes | yes | ? | pending |
| Observable-root localization | yes | yes | ? | pending |
| Incorrect-clean blocked by joint gate | yes | yes | ? | pending |
| Correct-but-poisoned blocked | yes | positive absent | ? | pending |
| Memory improves diagnosis | no | no | not tested | NOT CLAIMED |
| Role routing improves safety | no | no | not tested | NOT CLAIMED |
| ATGG improves natural tasks | unproven | low-trigger | not tested | NOT CLAIMED |

S5B 完成后自动填 final heldout 一列。

---

# 20. S5B 最终成功标准

S5B 不以“结果必须好看”为成功标准。

S5B 成功指：

1. 58/58 按冻结协议执行，或基础设施失败有完整透明记录；
2. 无算法/协议修改；
3. seal / ledger / replay 不出现系统性破坏；
4. gold leakage = 0；
5. controlled anomaly 结果能够完整计算；
6. Gate 的可用 denominator 被真实报告；
7. positive success 稀缺时不改变规则；
8. ablation 可从 artifact 自动重算；
9. 最终表格可一键生成；
10. claim matrix 与真实证据一致。

最终结论可以是：

```text
FINAL_EVIDENCE_COMPLETE
```

即使：
- success class 很少；
- Match 不高；
- 某些自然风险稀疏。

---

# 21. S5B 强制性交付

```text
S5B_final_heldout/
├── S5B_report.md
├── FINAL_REVIEW_REQUEST.md
├── Final_Core_v1_freeze_manifest.json
├── FINAL_CLAIM_MANIFEST.md
├── FINAL_DATA_ACCESS_LOG.json
├── run_manifest_80_137.csv
├── heldout_original/
├── seals/
├── outcome_results.csv
├── judge_raw/
├── diagnostic_metrics.csv
├── natural_audit_summary.csv
├── natural_experience_summary.csv
├── controlled_mutations/
├── anomaly_injection_ground_truth.jsonl
├── anomaly_detection_results.csv
├── anomaly_class_metrics.csv
├── root_localization_results.csv
├── gate_candidates.jsonl
├── gate_results.csv
├── gate_baseline_metrics.csv
├── module_ablation_matrix.csv
├── overhead_results.csv
├── leakage_check.json
├── case_studies/
├── tables/
├── figures/
├── final_claim_evidence_matrix.md
├── final_limitations.md
├── reproduce_final.sh
├── requirements_lock.txt
├── failure_cases.md
├── tests/
├── git_diff.patch
└── git_commit.txt
```

---

# 22. S5B_report.md 必须回答

1. 58 个病例是否全部按 freeze 运行？
2. 是否发生任何需要重跑的基础设施错误？
3. 是否发生会使 final heldout 无效的算法/协议 bug？
4. Held-out Match / Relevant 是多少，CI 如何？
5. strict outcome 中 success / incorrect / uncertain 分布？
6. heldout P0/P1/P2/P3 分布？
7. 自然 P3 出现频率和 P3-used 如何？
8. clean alert rate 如何？
9. Controlled A1/A2/A3/A5 的最终 per-class F1？
10. Observable-root Top-1？
11. secondary anomaly 如何处理？
12. final heldout 有多少 verified-success？
13. correct-but-poisoned 的最终 denominator 是多少？
14. 如果 sparse/absent，论文 Gate 正面结论如何降级？
15. Incorrect-clean Gate 对比结果？
16. Core ablation 是否支持每个模块的设计作用？
17. Local TraceEvo overhead？
18. gold leakage 是否为 0？
19. 哪些 claim 最终获得 heldout 支持？
20. 哪些 claim 仍仅由 development 支持？
21. 第一篇论文最终应该声称什么？
22. 第一篇论文明确不应该声称什么？

---

# 23. S5B 停止条件

完成后：

**停止所有算法开发与额外病例实验。**

不得：

- 修改 Core 后重跑 case 80–137；
- 为 success class 不足追加新数据；
- 开启 Memory Injection；
- 开启 ATGG；
- 开启 role routing；
- 更改 Judge；
- 调 P0–P3 threshold；
- 调 Gate；
- 用 case 80–137 继续开发。

下一阶段只允许：

```text
结果审计
统计汇总
论文撰写
图表整理
代码清理/复现脚本
```

不再进行方法开发。

---

# 24. 给本地科研智能体的启动指令

> 阅读 `TraceEvo_S4B_Review_S5A_Method_Freeze_v1.md`、`S5A_protocol_validation/S5A_report.md`、`S5A_protocol_validation/REVIEW_REQUEST.md`、`S5A_protocol_validation/failure_cases.md` 与本文件。S5A 判定 `GO_S5B_FINAL_HELDOUT`。在首次读取 case 80 之前生成 Final_Core_v1_freeze_manifest、FINAL_CLAIM_MANIFEST 和 FINAL_DATA_ACCESS_LOG。仅使用 case 80–137，共58例，执行一次性 final heldout。Core-v1 固定为 Dual-Graph Auditable Reasoning + Audit-Gated Trustworthy Experience Consolidation；Memory injection、Failure Memory activation、role adaptation、ATGG 全部关闭。每例必须先运行、落盘、seal、校验，再读取 gold 做 frozen OutcomeVerifier；任何经验不得注入后续病例。58条自然 trace 全部封存后，才在 artifact 副本上运行 A1/A2/A3/A5 controlled injection、root localization、Gate baseline 和 Core ablation。若 verified-success 稀缺或为0，按预注册规则报告，不修改 Judge/Gate、不追加病例。若发现影响算法结果的 Core bug，立即停止并标记 FINAL_HELDOUT_INVALIDATED，不得修复后重用 80–137 作为 final。完成所有交付物和 FINAL_REVIEW_REQUEST.md 后停止算法开发。
