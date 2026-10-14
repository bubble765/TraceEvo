# TraceEvo 科研实施计划 v1.0
## ——基于 Graph of States 的医学多智能体审计溯源与反馈驱动可信自进化

> 用途：本计划面向本地科研智能体执行。执行者原则上仅需阅读本计划与论文《Graph of States: Solving Abductive Tasks with Large Language Models》（GoS），即可从 GoS 基线复现开始，逐阶段完成代码扩展、实验验证与论文证据积累。
>
> 执行原则：严格按阶段推进；每一阶段完成后停止，整理“阶段性交付包”，由人工/外部评审确认后再进入下一阶段。未经确认，不得跳过基线复现直接开发完整方法。
>
> 工作名称：TraceEvo（暂定）
>
> 目标论文方向：边云协同医学多智能体中的可信执行、审计溯源与反馈驱动自进化。

---

# 1. 研究目标与论文核心问题

GoS 已针对医学诊断等溯因推理任务提出双层神经符号框架：认知层由中心智能体与专家智能体完成规划、工具调用和分析；符号层利用因果图与状态机显式维护推理状态，通过 `FindFocus → Plan → ReAct → UpdateGraph → StateConversion → CheckReport` 循环控制推理过程，并利用 backtracking 与 drill-down 避免证据伪造、上下文漂移、回溯失败和过早停止。

GoS 解决的是“一次复杂任务执行过程中，如何让多智能体推理更有结构、更可控、更容易收敛”。

本研究进一步解决：“多轮、多任务运行过程中，如何记录并验证多智能体的执行过程，识别异常及错误传播，并保证智能体只从可信、可追溯的历史经验中持续进化”。

## 1.1 研究假设

H1：GoS 的因果图主要表达症状、证据与假设之间的逻辑关系，不能完整表达“谁在何时调用了什么模型/工具、读取了什么证据、产生了什么结果、如何影响后续结论”的执行来源关系。

H2：在 GoS 执行过程中同步建立时序异构协作溯源图，并将其与 GoS 因果图交叉关联，可以提高异常识别、错误起点定位、传播路径回溯和结论证据溯源能力。

H3：若历史轨迹未经验证直接进入经验记忆，错误分析、伪造证据或异常调用可能被后续任务重复利用；利用审计结果对经验进行可信门控，可以降低错误经验写入和重复错误传播。

H4：在可信经验门控基础上持续更新智能体能力画像、经验记忆和协作策略，可在不修改基础大模型参数的情况下形成一种非参数、受控、可审计的智能体自进化机制。

---

# 2. 本论文的范围边界

## 2.1 必做范围

1. GoS 执行过程的双图建模：保留 GoS Causal Graph，同时新增 Provenance Graph，并建立两者的交叉映射。
2. 基于溯源图的异常审计与路径回溯：识别异常事件、异常节点和错误传播路径。
3. 审计门控的反馈驱动可信自进化：只有通过审计验证的经验才允许进入长期经验库，并用于更新能力画像与后续协作策略。

## 2.2 暂不作为主要创新的内容

- 跨语言医学模型能力本体；
- 完整国际化分布式语义网络；
- 大规模多目标资源调度算法；
- 联邦训练算法；
- 基础模型参数微调；
- 强化学习式长期策略训练。

第一版论文必须优先形成一个可复现、可做消融、可测量的“审计—进化”闭环，避免系统范围过大。

---

# 3. GoS 基线：必须保持的核心逻辑

执行者首先通读 GoS 第 3 节、Algorithm 1、Algorithm 2、医学诊断实验和附录 C.1。

GoS 的基础状态定义保留：

`B_t = (G_t, S_t)`

其中：
- `G_t`：GoS Causal Graph；
- `S_t`：GoS State Machine 当前层级；
- `h*_t`：当前 Reasoning Focus。

GoS 主循环在阶段 S0 中不得修改：

```text
h*_t = FindFocus(G_t)
Instructions = Plan(Acentral, h*_t, B_t)
Odeep, analyses = ReAct(Aexperts, Instructions, h*_t, B_t)
G_(t+1) = UpdateGraph(Acentral, analyses, G_t)
S_(t+1), G*_(t+1) = StateConversion(Acentral, G_(t+1), S_t)
ReportFlag = CheckReport(G*_(t+1))
```

GoS 医学实验以 DiagnosisArena 为基础，将原始“全部检查结果一次给出”的方式改造成动态调查任务：初始只给主诉和基本体检，其余检查结果存储在外部信息库中，智能体必须主动申请检查才能得到深层证据。本研究沿用这一设置。

---

# 4. TraceEvo 总体设计

```text
GoS 当前任务推理
    ↓
关键操作事件采集
    ↓
时序异构协作溯源图 P_t
    ↓
规则 / 图结构 / 证据一致性联合审计
    ↓
异常识别 + 根因定位 + 传播路径回溯
    ↓
轨迹可信评分
    ↓
经验门控
 ┌────────────┬────────────┬────────────┐
 │可信成功经验│可信失败经验│存疑/污染经验│
 └────────────┴────────────┴────────────┘
    ↓              ↓             ×
经验记忆        失败记忆        禁止写入
    ↓
能力画像更新 + 协作策略调整
    ↓
下一任务 GoS Plan / Agent Selection
```

关键思想：

- Reasoning Graph 解释“为什么得出这个医学结论”；
- Provenance Graph 解释“这个结论是由谁、通过哪些操作、基于哪些证据产生的”；
- Audit Gate 决定“这段经历是否有资格成为未来经验”。

---

# 5. 新增数据结构

## 5.1 审计事件 ProvenanceEvent

每个关键操作必须生成标准事件。最低字段如下：

```json
{
  "event_id": "...",
  "task_id": "...",
  "step_id": "...",
  "timestamp": "...",
  "agent_id": "...",
  "agent_role": "...",
  "action_type": "...",
  "tool_or_model": "...",
  "input_refs": [],
  "output_refs": [],
  "evidence_refs": [],
  "hypothesis_refs": [],
  "model_version": "...",
  "status": "success|failed",
  "parent_event_ids": [],
  "prev_hash": "...",
  "event_hash": "..."
}
```

第一版 `action_type` 至少覆盖：`PLAN`、`AGENT_CALL`、`TOOL_CALL`、`EVIDENCE_READ`、`ANALYSIS`、`GRAPH_UPDATE`、`BACKTRACK`、`DRILL_DOWN`、`FINAL_REPORT`。

事件使用 canonical JSON 计算哈希。`prev_hash` 只用于实验中的完整性验证，不要求实现区块链。

## 5.2 时序异构协作溯源图

定义 `P_t = (V^P_t, E^P_t)`。

推荐节点类型：Agent、Action/Event、Tool/Model、Evidence、Hypothesis、Conclusion。

推荐边类型：INVOKE、READ、GENERATE、SUPPORT、REFUTE、DEPEND、PROPAGATE。

所有节点必须携带 `task_id` 和时间信息。

## 5.3 GoS 因果图与溯源图的交叉映射

定义映射 `φ: V^P_(evidence,hypothesis) → V^G`。

要求：
- Provenance Graph 中 Evidence 节点能够映射到 GoS 中对应 `vevi`；
- Provenance Graph 中 Hypothesis 节点能够映射到 GoS 中对应 `vhyp`；
- GoS 每一次 `UpdateGraph` 都必须能够追溯到产生该变化的事件集合。

---

# 6. 异常审计方法

第一版优先做可解释、可控的“规则+图”方法，不要一开始训练复杂 GNN。

## 6.1 四类核心审计信号

完整性：
`C(τ) = 1 - N_missing / N_required`

能力/权限边界：
`B(τ) = 1 - N_violation / N_checked`

结论—证据一致性：
检查证据是否存在于外部 repository、版本是否一致、support/refute 边是否对应真实读取事件、最终结论是否可回溯到原始证据，记为 `E(τ) ∈ [0,1]`。

路径一致性：
检查关键中间结论是否存在来源断点、异常跳转和未授权传播，记为 `P(τ) ∈ [0,1]`。

## 6.2 轨迹可信度

第一版默认：

`Trust(τ) = wc*C(τ) + wb*B(τ) + we*E(τ) + wp*P(τ)`

初始设置 `wc = wb = we = wp = 0.25`。该权重仅为初始工程设置，阶段 S5 前必须做敏感性分析。

若发生以下 Critical Violation，则无论总分多少均禁止进入可信经验库：
- 使用不存在的证据；
- 证据被篡改或版本无法核验；
- 明确越权工具调用；
- 最终结论无法回溯到任何有效证据。

---

# 7. 审计反馈驱动的可信自进化

第一版自进化不训练 LLM 参数，采用“经验记忆 + 能力画像 + Planner 提示更新”，保证实现成本可控且容易做消融。

## 7.1 经验单元

```json
{
  "experience_id": "...",
  "task_summary": "...",
  "initial_context": "...",
  "reasoning_strategy": "...",
  "key_evidence_pattern": "...",
  "outcome": "...",
  "audit_score": 0.0,
  "critical_violation": false,
  "error_type": "...",
  "lesson": "...",
  "source_trace_id": "..."
}
```

经验禁止保存患者身份信息；若原数据中存在身份字段，进入 Experience Memory 前必须去标识化。

## 7.2 三态经验门控

设可信阈值 `θ`，初始 `θ = 0.8`。

```text
若 outcome 正确 && Trust >= θ && 无 Critical Violation
    → Validated Success

若 outcome 错误，但异常原因已成功定位 && Trust >= θ
    → Validated Failure

其余
    → Uncertain / Rejected
```

只有前两类可以参与后续任务。`Validated Failure` 只能作为“避错经验”，不得作为事实知识注入。

## 7.3 经验记忆

```text
Memory
├── success/
├── failure/
└── rejected/
```

在 GoS `Plan()` 前，按照当前任务症状、当前 reasoning focus、历史错误类型进行检索。注入 Planner 的内容必须采用“建议/风险提醒”形式，不得直接注入历史病例最终答案。

## 7.4 能力画像

每个智能体维护：
- success_rate
- audit_pass_rate
- mean_latency
- mean_token_cost
- evidence_quality
- recent_error_types

仅使用通过经验门控的数据更新。

第一版采用 EMA：

`c_(i,k)^(t+1) = (1-ρ)c_(i,k)^t + ρ*y_(i,k)^t`

默认 `ρ = 0.1`，阶段 S5 前做敏感性分析。

## 7.5 对 GoS 的反馈位置

最小改动只影响 `Plan()` 和专家智能体选择：

```text
原始：
Instructions = Plan(Acentral, h*, B)

修改：
trusted_memory = RetrieveTrustedExperience(current_task, h*)
profiles = LoadCapabilityProfiles()
Instructions = Plan(Acentral, h*, B, trusted_memory, profiles)
```

禁止在第一版中修改基础模型权重。

---

# 8. 人工异常注入机制

为了获得可验证的异常 ground truth，必须实现可控注入器。

| ID | 异常类型 | 注入方式 | Ground Truth |
|---|---|---|---|
| A1 | Fabricated Evidence | 将不存在于 repository 的证据写入分析/图 | 伪造事件及后续路径 |
| A2 | Evidence Version Mismatch | 替换证据版本或绑定错误证据 | 错误证据节点 |
| A3 | Unauthorized Tool Call | 指定不允许的专家调用工具 | 越权 event |
| A4 | Corrupted Intermediate Result | 修改一个专家分析结果并向后传播 | 起点 + 下游传播路径 |
| A5 | Trace Deletion | 删除一个关键 event 或破坏 hash link | 缺失 event |

可选：重复工具调用/Context Drift、Early Stopping、错误 Backtracking。

每次注入必须自动保存：
`injection_type`、`injection_event_id`、`ground_truth_root`、`ground_truth_affected_events`、`ground_truth_affected_conclusions`。

---

# 9. 实验设计

## 9.1 基线

- B0：GoS
- B1：GoS + Naive Memory
- B2：GoS + Audit Only
- B3：GoS + Ungated Evolution
- Ours：TraceEvo

## 9.2 主要实验

E1：GoS 基线复现  
指标：Match、Relevant、token/case、latency/case、API cost/case（若可获得）。

E2：审计与异常定位  
指标：Event anomaly Precision/Recall/F1、Root-cause localization Accuracy、Propagation-path Recall、Evidence-trace success rate、Key-operation record coverage。

E3：可信自进化  
观察随已验证经验数量增长后的 Match/Relevant、重复错误率、平均工具调用次数、token/case、latency/case。

E4：污染经验鲁棒性  
重点比较 Naive Memory、Ungated Evolution、TraceEvo。核心指标：Poisoned Experience Admission Rate、Repeated Error Rate、Match/Relevant degradation。

E5：消融  
至少包括 w/o Provenance Graph、w/o Cross-Graph Verification、w/o Experience Gate、w/o Capability Profile。

E6：额外开销  
统计 token、latency、storage、event count、graph size。

---

# 10. 数据划分和实验纪律

第一轮开发只取 10–30 个病例快速迭代，禁止在全量病例上边调参边测试。

推荐：

```text
Dev-Small：10–30 cases
用于代码正确性和快速调试

Dev-Evolve：用于经验积累和超参数调整

Held-out Test：最终结果，只在方法冻结后运行
```

若可用病例数与 GoS 论文一致，则以论文清洗后的有效病例为全集；具体有效数量由执行者根据 GoS 数据脚本实际确认，不得靠猜测写死。

所有最终实验必须满足：
- 固定 backbone model；
- 固定 temperature 等模型参数；
- 固定 retrieval budget；
- 固定最大交互轮数；
- 所有 baseline 使用同一 backbone 和预算；
- 保存完整 prompts、raw outputs、events、graphs、metrics；
- 结果不可人工修改。

若无法使用 GoS 论文中的相同模型：
1. 记录替代模型名称与版本；
2. 不强求复现论文绝对数值；
3. 所有方法必须在同一替代模型上公平比较；
4. 论文中明确说明模型差异。

---

# 11. 代码组织建议

禁止一开始大规模重构 GoS。建议新增独立目录并以 hook 方式接入。

```text
traceevo/
├── audit/
│   ├── event_schema.py
│   ├── event_logger.py
│   ├── provenance_graph.py
│   ├── rules.py
│   ├── anomaly_detector.py
│   └── path_tracer.py
├── evolution/
│   ├── experience.py
│   ├── experience_gate.py
│   ├── memory_store.py
│   ├── capability_profile.py
│   └── replay_verifier.py
├── injection/
│   ├── base.py
│   └── medical_injections.py
├── evaluation/
│   ├── audit_metrics.py
│   ├── evolution_metrics.py
│   └── overhead_metrics.py
└── configs/
    ├── traceevo.yaml
    └── injection.yaml
```

实际接入 GoS 时，不假定 GoS 源码存在特定文件名。执行者需依据论文 Algorithm 1 在源码中定位语义对应函数：Initialization、FindFocus、Plan、ReAct、UpdateGraph、StateConversion、CheckReport、Report，并记录“论文步骤 → 实际源码函数/文件”的映射表。

---

# 12. 阶段计划与强制性交付

## S0：GoS 基线复现与代码定位

目标：不修改 GoS 核心逻辑，跑通医学诊断。

任务：
- 获取论文给出的官方代码；
- 建立独立 Git 分支；
- 记录环境、依赖、模型配置；
- 找到 Algorithm 1/2 对应代码；
- 跑通至少 10 个医学病例；
- 复算 Match/Relevant；
- 保存每例完整输出。

交付包 `S0_baseline/`：

```text
S0_report.md
environment.txt
gos_code_mapping.md
config.yaml
results.csv
raw_outputs/
run.log
git_commit.txt
```

S0_report.md 必须回答：
- GoS 是否可成功运行？
- 使用什么模型？
- 与论文配置有哪些不同？
- 10 个病例 Match/Relevant 如何？
- Algorithm 1 每个函数对应哪个源码位置？
- 当前最明显的复现问题是什么？

进入 S1 的门槛：
- 10 个病例可稳定执行；
- 指标脚本可重复运行；
- 不存在未解释的严重运行错误。

**完成后停止，把 S0 交付给人工评审。**

## S1：关键事件采集与 Provenance Graph

目标：在不显著改变 GoS 输出的情况下完成全流程事件记录。

任务：
- 实现 ProvenanceEvent；
- 为 Algorithm 1 各关键步骤增加 hook；
- 实现 JSONL 日志；
- 构建 Provenance Graph；
- 实现 Causal Graph ↔ Provenance Graph 映射；
- 绘制 2–3 个病例的溯源图。

交付包 `S1_provenance/`：

```text
S1_report.md
schema.json
sample_trace.jsonl
sample_provenance_graph.json
sample_graph.png
cross_graph_mapping.json
unit_tests/
before_after_outputs.csv
git_diff.patch
```

必须验证：
- 所有 GoS `UpdateGraph` 都能找到对应来源事件；
- 最终诊断能够回溯到 Evidence；
- 加入日志后，GoS 基线诊断结果不应发生系统性变化；
- 10 个开发病例关键事件记录覆盖率达到 100%（开发检查，不是论文最终声称）。

**完成后停止，等待评审。**

## S2：异常注入与审计追溯

目标：验证溯源图能够用于发现和回溯异常。

任务：
- 完成 A1–A5 注入器；
- 实现完整性、权限、证据一致性、路径一致性审计；
- 实现异常根因定位；
- 实现传播路径回溯；
- 自动生成审计报告。

交付包 `S2_audit/`：

```text
S2_report.md
injection_spec.md
audit_rules.md
injected_cases.csv
audit_results.csv
root_cause_results.csv
example_audit_report.json
example_path.png
failure_cases.md
git_diff.patch
```

阶段验收目标（工程门槛，不是最终论文结论）：
- 5 类异常均能被流程处理；
- 在确定性注入异常上，Anomaly F1 和 Root-cause Accuracy 原则上应达到 0.80 以上；
- 若达不到，禁止直接进入自进化阶段，应先分析失败类型。

**完成后停止，等待评审。**

## S3：审计门控的可信经验记忆

目标：证明“审计结果能够控制经验是否进入长期记忆”。

任务：
- 实现 Experience；
- 实现三态 Experience Gate；
- 实现 Success/Failure/Rejected Memory；
- 实现 trusted memory retrieval；
- 在 `Plan()` 前注入可信经验；
- 实现 Replay Verification 的最小版本。

必须先做受控实验：

```text
GoS
GoS + Naive Memory
GoS + TraceEvo Memory Gate
```

交付包 `S3_memory_gate/`：

```text
S3_report.md
experience_schema.json
memory_examples/
gate_results.csv
poison_admission.csv
diagnosis_results.csv
prompts/
failure_cases.md
git_diff.patch
```

进入 S4 的门槛：
- 明确证明错误经验可导致 Naive Memory 出现负面影响；
- TraceEvo 能明显降低错误经验写入率；
- 正常可信经验仍可进入 Memory；
- 不出现 ground-truth leakage。

**完成后停止，等待评审。**

## S4：能力画像与反馈驱动自进化

目标：形成完整“审计—评估—反馈—进化”闭环。

任务：
- 实现 CapabilityProfile；
- 从可信经验更新成功率、审计通过率、时延、token、证据质量；
- 将画像反馈到 Planner / Expert Selection；
- 开展连续多批次任务实验；
- 观察能力画像、经验库和诊断性能随轮次变化。

交付包 `S4_evolution/`：

```text
S4_report.md
profile_snapshots/
evolution_curve.csv
evolution_curve.png
memory_growth.csv
agent_selection_log.csv
diagnosis_results.csv
overhead_results.csv
git_diff.patch
```

阶段成功判据：
- 能力画像随经审计的任务反馈发生可解释变化；
- 可信经验能够改变后续 Planner/Agent 行为；
- TraceEvo 不应因经验积累导致错误率持续恶化；
- 在污染经验场景下，TraceEvo 应明显优于 Ungated Evolution；
- 有至少 2–3 个可解释 case study 展示“异常 → 审计 → 门控/更新 → 后续行为改变”。

**完成后停止，等待评审。**

## S5：全量实验与论文证据冻结

目标：不再改算法，只完成最终实验。

运行：E1–E6。

交付包 `S5_final_exp/`：

```text
S5_report.md
all_configs/
all_results/
all_raw_outputs/
all_traces/
tables/
figures/
statistical_analysis.md
limitations.md
reproduce.sh
requirements.txt
git_commit.txt
```

必须生成可直接用于论文的：
- 表1：主要方法诊断性能；
- 表2：异常检测与路径回溯；
- 表3：污染经验鲁棒性；
- 表4：消融实验；
- 表5：运行开销；
- 图1：TraceEvo 总体框架；
- 图2：Causal Graph 与 Provenance Graph 双图示例；
- 图3：可信进化曲线；
- 图4：典型异常追溯 Case Study。

---

# 13. 每阶段返回评审的统一格式

每完成一个阶段，必须输出 `REVIEW_REQUEST.md`：

```markdown
# Stage Sx Review Request

## 1. 本阶段完成内容
...

## 2. 与计划相比的偏差
...

## 3. 核心结果
...

## 4. 最成功的 3 个结果
...

## 5. 最失败的 3 个结果
...

## 6. 尚未解决的问题
...

## 7. 关键代码改动
...

## 8. 建议下一阶段是否继续
GO / STOP / NEED_REVISION

## 9. 需要外部评审回答的问题
1. ...
2. ...
3. ...
```

用户后续只需把 `REVIEW_REQUEST.md`、核心 CSV、关键图和必要代码 diff 返回给外部评审，即可快速判断是否修正计划后继续。

---

# 14. 严格研究纪律

1. 先复现，后创新。S0 未通过，不得进入完整算法开发。
2. 保留 GoS 原始 baseline，禁止直接覆盖修改。
3. 所有创新必须可消融，每个模块都必须可配置关闭。
4. 不允许测试集泄漏，最终测试病例不得进入经验库。
5. 经验不得直接存最终答案作为检索提示，应保存策略、证据模式和错误教训。
6. 所有异常必须有 ground truth。
7. 所有 LLM Judge 结果必须保留 prompt 和 response。
8. 每次实验保存模型名称、版本、温度、预算、seed、commit hash。
9. 失败实验不得删除，记录到 `failure_cases.md`。
10. 如果某阶段核心假设不成立，应修改研究计划，而不是堆更多模块。

---

# 15. 第一篇论文的预期贡献表达

若最终实验支持假设，论文可凝练为三点：

**贡献1：双图耦合的医学多智能体执行溯源机制。**  
在 GoS 因果推理图之外构建时序异构协作溯源图，并通过证据与假设映射关联推理逻辑和真实执行来源，实现多智能体医学推理的细粒度可追溯。

**贡献2：面向错误传播的协作审计与路径回溯方法。**  
联合事件完整性、能力边界、证据一致性和图路径关系识别异常，定位异常起点并恢复受影响的推理传播路径。

**贡献3：审计证据门控的可信自进化方法。**  
利用任务结果与审计证据筛选可信成功/失败经验，动态更新经验记忆和智能体能力画像，并反馈优化后续协作策略，实现可验证、受控的多智能体持续演进。

---

# 16. 当前执行顺序

```text
S0 GoS 复现
   ↓
S1 Provenance Instrumentation
   ↓
S2 Audit + Anomaly Injection
   ↓
S3 Trusted Experience Gate
   ↓
S4 Capability Profile + Evolution
   ↓
S5 Full Evaluation
   ↓
论文写作与投稿
```

**首次执行时，只执行 S0。完成 S0 后停止，不得自动进入 S1。**
