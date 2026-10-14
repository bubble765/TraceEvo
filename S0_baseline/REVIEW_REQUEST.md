# Stage S0 Review Request

> 按计划 §13 统一格式提交。请评审 §8 的 GO / STOP / NEED_REVISION 建议与 §9 的 3 个问题。

---

## 1. 本阶段完成内容

计划 §12 的 S0 六项任务全部完成：

| 任务 | 状态 |
|---|---|
| 获取论文官方代码 | ✅ `https://github.com/gaorch85/Graph-of-States` @ `0f28c2e`，未修改地保留在 `gos_official/` |
| 建立独立 Git 分支 | ✅ 工作副本 `gos/`，分支 `traceevo-s0`，`main` 分支 = 上游原样导入 |
| 记录环境、依赖、模型配置 | ✅ `environment.txt`、`config.yaml` |
| 找到 Algorithm 1/2 对应代码 | ✅ `gos_code_mapping.md`（Algorithm 1 全 8 个语义函数 + Algorithm 2 全 6 个语义步骤 + 数据结构映射） |
| 跑通至少 10 个医学病例 | ✅ case 0–9 共 10 例，全部产出预测与报告 |
| 复算 Match/Relevant | ✅ `evaluate_Medical_scoring.py` + `results.csv`（上游官方 judge 脚本不产出指标，见 §7） |
| 保存每例完整输出 | ✅ `raw_outputs/`（轨迹 / 因果图 / 摘要） |

---

## 2. 与计划相比的偏差

| 项 | 计划 | 实际 | 原因 |
|---|---|---|---|
| Backbone | GoS 论文 `gpt-5.1` | `deepseek-v4-flash`（本机网关） | `api.chatanywhere.tech` 无可用 key；已按计划 §10 第 2 条记录替代模型 |
| Human-as-a-Judge | 计划 §9.1 基线需 LLM + 人工双评 | **仅 LLM-as-a-Judge** | 无可用的临床评审人；已在报告 §5 明确标注 |
| 病例数 | 「至少 10 个」 | 10 个（Dev-Small） | 符合计划 §10 纪律，未触碰 case 10–137 |
| Judge 重复次数 | 计划未要求 | 额外做了 6 次重复评分以量化噪声 | 发现噪声（Match ±8.4pp）与预期效应量同量级，见 §6 问题 1 |
| 代码修改 | §11「禁止大规模重构」 | 198 行基础设施补丁，主循环语义零改动 | 上游开箱不可运行（5 处必修） |

---

## 3. 核心结果

**诊断指标（case 0–9，LLM-as-a-Judge，6 次重复评分）**

| | S0（10 例，deepseek-v4-flash） | 论文（138 例，GPT-5.1） |
|---|---|---|
| Match | **25.0 ± 8.4%**（单次极差 20–40%） | 31.88% |
| Relevant | **61.7 ± 7.5%**（单次极差 50–70%） | 74.64% |

**在 ±8pp 的 judge 噪声下，10 例样本无法区分 25% 与 32%**，「数值接近」不构成复现成功性证据。

**基线自身的量化缺陷（S0 最重要的产出）**

1. `UpdateGraph` 新增的 47 个 Evidence 节点中，**27.7% 无法回溯到任何来源**；Evidence 类型被混用作教科书知识、专家结论、建议的容器（10 例共 80 个 Evidence 节点，完整分类见 `evidence_grounding_analysis.csv`）。
2. `central_agent.graphs` 的所有图快照**在内存中是同一份数据**（`to_dict()` 返回引用），10 例中每例快照哈希完全一致 → 计划 §5.3 的交叉映射**无法靠现有代码实现**。
3. 回溯机制 10 例仅触发 3 次；`min_support=3` 的 drill-down 判据依赖上面第 1 条中语义不可信的 `support` 边。

**Judge 噪声**：同一批预测重复评分 6 次，Match 20–40%、Relevant 50–70%（标准差约 8pp）。逐例看，**7/10 病例评分完全一致，3/10 不稳定（case 2、4、8）**，且不稳定是双向的（case 2 既给过 2 分也给过 0 分）。论文 Figure 8 报告 10 例中 4 例不一致，量级一致。

---

## 4. 最成功的 3 个结果

1. **主循环语义 100% 保真复现，且论文未写明的实现细节全部定位。**
   Algorithm 1/2 的每个语义函数都映射到了具体源码位置，并识别出 5 处论文未描述但代码中确实存在的机制（`max_steps` 强制推进、`graph.belief` 文本共识模块、`parent_edges[0]` 近似、删除不级联、δ/η 取值来源）。这为 S1 的 hook 布点提供了精确坐标。

2. **用基线自己的数据证明了研究假设 H1 成立。**
   不是靠论证「GoS 理论上无法溯源」，而是实测出 27.7% 的新增 Evidence 节点无来源、Evidence 语义混用 5 类内容。计划 §5.3 的 `φ` 映射因此有了明确的、可量化的设计目标。

3. **发现并提前化解了 S1 的两个致命前提错误。**
   (a) 图历史不存在于内存中，若按直觉复用 `central_agent.graphs` 会在 S1 后期才发现无数据可依；
   (b) `evaluate_Medical_scoring.py` 的 case 编号歧义在 10 例时就会静默丢分（首次运行 10 例只解析出 5 例），若不修，S3/S4 的进化曲线会建立在错误的评分上。

---

## 5. 最失败的 3 个结果

1. **Match 仅 30%，且多数错误是「同一疾病的不同粒度/机制表述」而非真正诊断错误。**
   例如 case 4 预测 `Acupuncture hemopneumothorax` vs 参考答案 `Traumatic hemopneumothorax with pulmonary hemorrhage`、case 9 预测 `Cardiac lymphoma infiltration` vs `Secondary cardiac lymphoma`——被 judge 判为 0 或 1，但从临床等价性看这些差异未必有意义。**当前 judge 对「医学等价表述」的宽容度不足，会系统性低估 GoS 能力，也会污染 S3/S4 的进化信号。**（这与论文承认 LLM judge「偏向表面文本相似度」一致。）

2. **10 例样本 + 单次评分完全不足以支撑任何结论。**
   Baseline 自身的 Match 在重复评分间波动 20–40%（标准差 8.4pp）。原计划「先跑 10 例快速迭代」在**指标层面**可行，在**结论层面**不可行。

3. **回溯（backtracking）这一 GoS 的核心机制在实测中几乎不激活（10 例 3 次）。**
   如果 TraceEvo 的异常注入主要依赖回溯路径来体现价值，那么 S2 可能面临「机制存在但从不触发、注入后无路径可回溯」的风险。尚不确定这是配置问题（`gap_delta`/`min_support`/`max_steps=2`）还是论文设置下的真实行为。

---

## 6. 尚未解决的问题

1. **Judge 噪声与计划 §9.2 的可行性冲突。**
   E3/E4 需要证明「可信自进化带来提升」。若提升幅度小于 judge 噪声（当前 10 例下标准差为 Match 8.4pp / Relevant 7.5pp），实验无法区分信号与噪声。**需要在 S1 开始前确定 E3/E4 的样本量与重复次数**，否则 S3/S4 会产出无法解释的结果。

2. **δ / η / `max_steps` 的取值是否忠实。**
   仓库配置为 `gap_delta=0.3`、`min_support=3`、`max_steps=2`，但论文正文只给出敏感性曲线图，未写死数值。无法确认作者跑出 31.88% 时用的就是这组值。**这直接影响「回溯几乎不触发」是真实行为还是配置产物。**

3. **GoS 的 Evidence 语义边界应由谁定义。**
   S1 的 Provenance Graph 需要把 Evidence 拆成 (真实读取 / 输入复述 / 缺失陈述 / 背景知识 / 结论建议)。这个分类学是本研究的**方法贡献**还是**工程预处理**？它会决定计划 §15 贡献 1 的表述强度。

4. **替代 backbone 的能力差异无法剥离。**
   `deepseek-v4-flash` 与 `GPT-5.1` 在长上下文遵循、JSON 稳定性上可能不同（实测 case 1 出现了 62 次调用）。所有基线都固定在同一模型上可以保证公平，但**绝对能力水平**的差异会使某些注入异常（如 A4 传播）的触发难度与论文设定不同。

---

## 7. 关键代码改动

补丁全部集中在 `gos/` 的 4 个文件，**GoS 主循环（Algorithm 1/2）语义零改动**，共 +198 / −42 行：

| 文件 | 改动 |
|---|---|
| `utils/public_function.py` | API key/base_url 外置为 `configs/LLM_Configs.yaml`；新增指数退避重试；修复未定价模型抛 `NameError`；`evidence_retriever` 硬编码 `gpt-5.1` → 配置读取 |
| `belief/graph.py` | `generate_belief_text` 硬编码 `gpt-3.5-turbo` → 配置读取（**上游管线中混入了第二个 backbone**） |
| `main.py` | 结果 CSV / HTML / 日志路径由硬编码改为 CLI 参数；新增 `--cases`；新增因果图 JSON 落盘；删除 pandas 2.x 不接受的 `escapechar` |
| `configs/Agent_Configs.yaml` | `model_path` 改为替代 backbone（附注释说明） |

新增（未改动上游文件）：`configs/LLM_Configs.yaml`、`evaluate_Medical_scoring.py`、`collect_s0_artifacts.py`、`build_s0_results.py`。

**未采纳的做法**：没有把 `max_tokens` 加入请求 payload（上游接收该参数、写进计费 meta、却从不发送），以保持与上游行为一致，差异记录在报告 §5。

---

## 8. 建议下一阶段是否继续

**GO —— 建议进入 S1，但附带 3 项前置修订。**

理由：S0 的三项门槛全部满足（10 例稳定执行、指标脚本可重复、无未解释错误），且 S0 产出的负面发现（Evidence 语义混用、图历史缺失）**反而使 S1 的设计目标比计划撰写时更清晰**，属于研究进展而非障碍。

进入 S1 前需要人工确认的 3 项修订：

1. **把「判别 Evidence 语义子类」写入 S1 的 schema 设计**（计划 §5.1 的 `action_type` 需要能区分 `EVIDENCE_READ` 与 `ANALYSIS`，且节点类型需扩展，不能沿用 GoS 的三分类）。
2. **明确 S1 必须自行深拷贝图状态**，不得复用 `central_agent.graphs`（计划 §11 未预见到这一点）。
3. **为 E3/E4 设定样本量下限与 judge 重复规范**，否则 S3/S4 的核心结论不可解释。

---

## 9. 需要外部评审回答的问题

1. **「回溯几乎不触发」应当被接受为基线事实，还是应当先调参复现论文的敏感性曲线？**
   10 例中仅 3 次回溯。若判定为配置产物，S0 需要补一轮 δ/η 扫描（额外约 5–8 小时机时）；若判定为真实行为，S2 的注入设计必须主动构造能触发回溯的场景。这个选择会显著改变 S2 的工作量。

2. **GoS 的 Evidence 被混用为「背景知识/结论/建议」容器——这是应当由 TraceEvo 修正的缺陷，还是应当保留的上游特性以便公平对比？**
   若在 Ours 中修正而 baseline 不修正，性能差异可能来自「更干净的图」而非「审计与进化」，会削弱贡献 2/3 的归因。建议明确：**修正应当作为独立的消融项**（对应计划 E5 的 `w/o Cross-Graph Verification` 之外，是否需要新增 `w/o Evidence Typing`）。

3. **在无 Human-as-a-Judge 的条件下，应当如何设定 S3/S4 的成功判据？**
   建议候选方案：(a) 用多次 judge 重复的均值±方差，以「提升 > 2σ」为判据；(b) 改用确定性指标（如「最终预测是否命中参考答案的关键实体」）作为主指标、judge 作为辅助；(c) 招募标注者做小样本人工评分。请评审指定一种，S1 起统一使用。
