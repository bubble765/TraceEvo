"""双图数据协议。

这是前后端唯一的契约。演示模式与真实分析模式返回**完全相同**的结构 ——
前者从封存 artifact 读，后者从正在运行的病例读，前端无法也不应该分辨。

设计要点，每一条都对应一个已核实的事实：

* **推理图从 GraphDelta 重建**，不从 `_provenance_graph.json` 取。因果边
  （support/refute/refines）属于推理图；溯源图只记录事件依赖，跨图关系由
  事件节点的 `causalNodeRefs` 表示。
* **溯源图是事件 DAG**：节点直接对应事件，边只含 `src` / `dst`，表示父事件
  到子事件的依赖。旧版产物仍由服务层兼容读取。
* **id 一律带前缀**（`gos:` / `evt-` / `rec-`），因为 delta 用裸 `node-N`
  而展示协议用 `gos:node-N`；两套 id 混用会让「点节点高亮事件」静默失配。
* **算法判断字段只增不减**：tier / anomaly / outcome / gate 全部由后端
  产生并在协议里显式声明，前端类型定义为只读，没有本地推导路径。

所有面向用户的文字用 `i18n` 域 + 值的形式同时下发（`tier` + `tierLabel`），
前端不需要再查表，也不会因为查不到而显示空白。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from . import i18n

# ------------------------------------------------------------------ 通用
PREFIX_GOS = "gos:"


class CaseRef(BaseModel):
    """`/api/cases` 的一行。"""
    caseId: str
    caseNumber: int
    runId: str
    runLabel: str
    kind: str                       # sealed | live
    isDemo: bool
    outcomeLabel: Optional[str] = None
    gateLabel: Optional[str] = None


class CaseSummary(BaseModel):
    caseId: str
    runDir: str
    # "run"（跑出来的）或 "showcase"（手写展示轨迹）。界面据此说明
    # 「这一页有没有审计产物」—— 不能靠 tierCounts 全 0 去猜。
    kind: str = "run"
    auditApplicable: bool = True
    auditNote: str = ""
    nEvents: int
    nDeltas: int
    nRecords: int
    nNodes: int
    tierCounts: Dict[str, int]
    nAnomalies: int
    anomalyTypes: List[str]
    outcome: Optional[str] = None
    outcomeLabel: Optional[str] = None
    gate: Optional[str] = None
    gateLabel: Optional[str] = None
    # None = **不适用**（展示轨迹没有可校验的东西），False 才是「校验没通过」。
    # 用 bool 的话两者都是 False，界面只能显示「失败」——而一份手写轨迹
    # 从来没被校验过，说它「失败」是凭空指控。
    integrityOk: Optional[bool] = None
    replayOk: Optional[bool] = None
    prediction: Optional[str] = None


# ------------------------------------------------------------ 医学因果推理图
class RiskFlags(BaseModel):
    unsourced: bool = False
    anomalyRoots: List[str] = Field(default_factory=list)


class ReasoningNode(BaseModel):
    id: str                          # "gos:node-11"
    nodeId: str                      # "node-11"
    nodeType: str                    # Signal | Evidence | Hypothesis | Conclusion
    typeLabel: str
    # `label` 是中文展示文本（见 `zh/translator.py`），`labelEn` 是推理管线
    # 产出的英文原文。两个都下发是有意的：译文属于展示层，可以被打磨；
    # 原文属于事实，审计与「这句话真的是模型写的吗」的核对必须看它。
    # 只发译文会让界面上的每一句话都无法回溯。
    label: str
    labelEn: str = ""
    tier: Optional[str] = None
    tierLabel: Optional[str] = None
    tierDesc: Optional[str] = None
    # ---- 置信度 ----------------------------------------------------------
    # 这里写过两次错话，第二次是现在改的。第一版说「GoS 的节点置信度从不落盘，
    # 恒为 null」—— 那是在 bindings / provenance_graph / deltas 三类产物里找
    # 的结果，没往 events 里找。第二版改对了「落盘了」，但说成「只在 PLAN 事件
    # 的 payload.focus 里」—— 那只覆盖 28% 的假设。
    #
    # 真正的形状：置信度是**每步都记**的，主来源是 GRAPH_UPDATE 事件的响应
    # （`candidates[].confidence` / `nodes[].confidence` / `edit[].confidence`），
    # 按步累积成轨迹；PLAN 焦点读数补深挖节点的空位。详见 `services/belief.py`。
    #
    # 它是**运行实际产生的值**，不是参考项目 `belief/confidence.py` 那套工程化
    # 公式（z_t 累积 + 层内归一化）算出来的 —— 本仓库冻结的 `gos/` 把那段移除
    # 了，走的是「LLM 直出」。两者数值不同（case-85 的 node-5 是 0.85 对 0.95），
    # 这里给的是**前者**：审计工具要的是运行真正产生的那个数。
    #
    # 正常运行里 Hypothesis 的 confidence 是按步重建的；展示轨迹还可从
    # binding.score 读取参考图为 Signal / Evidence 保存的静态分数。
    # 「没有读数」必须与「读数是 0」分开。
    confidence: Optional[float] = None
    confidenceNote: str = ("没有读数时为 null；真实运行按步重建假设分数，展示轨迹保留参考图中的静态分数")
    # 该节点这次读数的来源事件；`confidenceDelta` 是相对**上一步**的变化量
    # （与参考项目角标同义：没被重新评估时是 0，不是 null）。
    confidenceEventId: Optional[str] = None
    confidenceDelta: Optional[float] = None
    # 这次读数附带的理由（`GRAPH_UPDATE` 的 `candidates[].why` /
    # `edit[].why`，或 PLAN 焦点读数的 `why`）。**与 confidence 同源同步** ——
    # 它描述的是「为什么是这个数」，所以必须跟那个数一起随时间轴走，
    # 不能当成节点上的一个静态字段。
    #
    # 非假设节点没有置信度读数，它们的 why 是**静态**的来源说明
    # （参考项目里 E3 是「影像医师开具·左踝正侧位」、KB104 是
    # 「KB104 · 避免确认偏误…」），从 bindings 取。一个字段两个来源，
    # 轨迹优先 —— 两者同时存在时，随时间变化的那个才是当前的事实。
    why: Optional[str] = None
    # 层号。主来源是 bindings（参考项目把 level 直接存在节点自身的 attrs 上，
    # 因此**每个**假设都有层级，而列式布局也必须知道每个假设的层级），
    # 没有时回落到规划焦点读数 —— 真实运行的 bindings 里没有这个键，
    # 走的仍是焦点读数那条老路。
    confidenceLevel: Optional[int] = None
    # 指向该假设的 support / refute 边数，**从回放的图里数出来**（不是读焦点
    # 读数），与焦点读数逐条核对过 114/114。非假设节点为 null —— 对一条证据说
    # 「支持 0 · 反驳 0」是把「不适用」说成了「零」。
    beliefSupports: Optional[int] = None
    beliefRefutes: Optional[int] = None
    # ---- 来源匹配度 ------------------------------------------------------
    # 该节点与它最接近的检索记录之间的覆盖度，持久化在 bindings.json 的
    # match_coverage 字段，取值 0.0–1.0。
    #
    # **它只在「绑定了记录」时才有意义。** bindings.json 里 599 条取值为 0.0
    # 的，绝大多数不是「匹配度低」，而是**压根没有可比对的东西**：
    #   match_tier=input  证据来自病例输入   → 不适用
    #   match_tier=n/a    Signal / Hypothesis → 本就不与检索记录比对
    #   match_tier=none   且无记录           → 检索过，但没有记录被绑定
    # 对信号与假设显示 0.00 是在回答一个没人问的问题，而读者会把它读成
    # 「匹配得很差」。所以：matchCoverage 只在有意义时给数，
    # 否则为 null 并由 matchNote 说明为什么。
    matchCoverageLabel: str = "来源匹配度"
    matchNote: Optional[str] = None
    # Showcase 没有审计判定，因此参与推理状态为 None。
    usedInReasoning: Optional[bool] = None
    usedLabel: Optional[str] = None
    createdByEvent: Optional[str] = None
    createdAtStep: Optional[int] = None
    sourceClass: Optional[str] = None
    sourceClassLabel: Optional[str] = None
    semanticKind: Optional[str] = None
    semanticKindLabel: Optional[str] = None
    matchTier: Optional[str] = None
    matchedRecordId: Optional[str] = None
    matchCoverage: Optional[float] = None
    bestAnalysisEvent: Optional[str] = None
    risk: RiskFlags = Field(default_factory=RiskFlags)


class ReasoningEdge(BaseModel):
    id: str
    source: str
    target: str
    relation: str                    # support | refute | refines
    relationLabel: str
    createdAtEvent: Optional[str] = None
    visibleFromStep: Optional[int] = None


class ReasoningGraph(BaseModel):
    caseId: str
    finalGraphHash: Optional[str] = None
    nodes: List[ReasoningNode]
    edges: List[ReasoningEdge]


# ------------------------------------------------------------ 执行溯源图
class IOSummary(BaseModel):
    input: str = ""                  # 中文展示文本
    output: str = ""
    inputEn: str = ""                # 英文原文
    outputEn: str = ""
    inputTruncated: bool = False
    outputTruncated: bool = False


class ProvenanceNode(BaseModel):
    id: str
    kind: str                        # plan | agent_call | ... | agent | tool | record
    kindLabel: str
    label: str                       # 中文展示文本
    labelEn: str = ""                # 英文原文（见 ReasoningNode.label 的说明）
    eventId: Optional[str] = None
    step: Optional[int] = None
    actor: Optional[str] = None
    actorLabel: Optional[str] = None      # 中文名，如「影像医师」
    actorIcon: Optional[str] = None       # 头像 emoji
    actorRole: Optional[str] = None
    actorRoleLabel: Optional[str] = None
    model: Optional[str] = None
    status: Optional[str] = None
    statusLabel: Optional[str] = None
    reasonCode: Optional[str] = None
    reasonCodeLabel: Optional[str] = None
    timestamp: Optional[str] = None
    eventHash: Optional[str] = None
    prevHash: Optional[str] = None
    parentEventIds: List[str] = Field(default_factory=list)
    inputRefs: List[str] = Field(default_factory=list)
    outputRefs: List[str] = Field(default_factory=list)
    causalNodeRefs: List[str] = Field(default_factory=list)
    io: IOSummary = Field(default_factory=IOSummary)
    anomalyRoot: bool = False        # 是可观测故障起点
    downstreamOfAnomaly: bool = False


class ProvenanceEdge(BaseModel):
    id: str
    source: str
    target: str
    relation: str
    relationLabel: str


class ProvenanceGraph(BaseModel):
    caseId: str
    nodes: List[ProvenanceNode]
    edges: List[ProvenanceEdge]


# ------------------------------------------------------------ 增量
class EdgeChange(BaseModel):
    source: str
    target: str
    relation: str
    relationLabel: str


class ConfidenceChange(BaseModel):
    nodeId: str
    before: Optional[float] = None
    after: Optional[float] = None


class GraphDeltaView(BaseModel):
    deltaId: str
    eventId: str
    # `step` 是**回放位置**（delta 序号，0 起），时间轴用它定位。
    # `agentStep` 是 GoS 的规划轮次（事件上的 step_id，0 起），两者不是
    # 一回事：一轮规划里可以有多次图更新（case-85 的轮次 0 就有两次）。
    # 协议里两个都给，前端不必从任一个去推另一个。
    step: Optional[int] = None
    agentStep: Optional[int] = None
    eventKind: Optional[str] = None
    eventKindLabel: Optional[str] = None
    actor: Optional[str] = None
    actorRoleLabel: Optional[str] = None
    timestamp: Optional[str] = None
    addedNodes: List[str] = Field(default_factory=list)
    removedNodes: List[str] = Field(default_factory=list)
    updatedNodes: List[str] = Field(default_factory=list)
    # 当前因果推理 frontier（按步回放的视觉提示，不是节点的审计结论）。
    focusNodeId: Optional[str] = None
    # Backtrack 时标记从哪个层级/节点退回，便于前端明确显示焦点迁移。
    backtrackFromNodeId: Optional[str] = None
    addedEdges: List[EdgeChange] = Field(default_factory=list)
    removedEdges: List[EdgeChange] = Field(default_factory=list)
    confidenceChanges: List[ConfidenceChange] = Field(default_factory=list)
    beforeHash: str
    afterHash: str
    beforeHashShort: str
    afterHashShort: str
    replayOk: Optional[bool] = None
    summaryLabel: str = ""
    # 这一步的标题。**手写展示轨迹会自带一个**（对齐参考项目的步骤名，
    # 如「知识会诊：RAG 检索临床推理经验」），真实运行的 delta 没有这个键，
    # 由前端回落到「事件类型 · 轮次 N」。见 `services/delta.py::_authored`。
    title: str = ""
    # 逐步解说。真实运行由 `_narrative()` 从事件事实生成；展示轨迹可以自带
    # 一段手写文案覆盖它。两者共用同一个字段，是因为界面上只有一处显示它 ——
    # 分两个字段会让「这一步到底在讲什么」有两个答案。
    narrative: str = ""
    isKeyStep: bool = False          # 本步首次引入 P3 无来源断言
    keyStepReason: str = ""


# ------------------------------------------------------------ 双向联动
class NodeEvents(BaseModel):
    nodeId: str
    createdByEvent: Optional[str] = None
    graphUpdateEvent: Optional[str] = None
    bestAnalysisEvent: Optional[str] = None
    upstreamEvents: List[str] = Field(default_factory=list)
    sourceEvents: List[str] = Field(default_factory=list)
    recordIds: List[str] = Field(default_factory=list)
    anomalies: List[str] = Field(default_factory=list)


class EventNodes(BaseModel):
    eventId: str
    created: List[str] = Field(default_factory=list)
    updated: List[str] = Field(default_factory=list)
    removed: List[str] = Field(default_factory=list)
    affectedHypotheses: List[str] = Field(default_factory=list)


class BindingIndex(BaseModel):
    caseId: str
    nodeToEvents: Dict[str, NodeEvents]
    eventToNodes: Dict[str, EventNodes]
    anomalyPaths: Dict[str, "AnomalyPath"] = Field(default_factory=dict)


# ------------------------------------------------------------ 审计
class ThresholdContext(BaseModel):
    directMinCoverage: Optional[float] = None
    softMinCoverage: Optional[float] = None
    softMinCharSimilarity: Optional[float] = None
    inputInferenceMinCoverage: Optional[float] = None
    expertSynthesisMinCoverage: Optional[float] = None
    minContentTokens: Optional[int] = None
    source: str = ""


class TierDecisionView(BaseModel):
    """一个节点为什么是某个等级。审计面板直接渲染这一条。"""
    nodeId: str
    label: str
    labelEn: str = ""
    nodeType: Optional[str] = None
    nodeTypeLabel: Optional[str] = None
    provenanceTier: str
    tierLabel: str
    tierDesc: str
    auditLabel: Optional[str] = None
    auditLabelText: Optional[str] = None
    derivation: Optional[str] = None
    derivationLabel: Optional[str] = None
    derivationKind: Optional[str] = None
    derivationKindLabel: Optional[str] = None
    bestRecordId: Optional[str] = None
    bestRecordHash: Optional[str] = None
    tokenCoverage: Optional[float] = None
    charSimilarity: Optional[float] = None
    bestSpanRatio: Optional[float] = None
    analysisCoverage: Optional[float] = None
    inputCoverage: Optional[float] = None
    usedInReasoning: bool = False
    usage: Optional[str] = None
    externallyGrounded: bool = False
    lineageComplete: bool = False
    bestAnalysisEvent: Optional[str] = None
    thresholds: ThresholdContext = Field(default_factory=ThresholdContext)


class AnomalyPath(BaseModel):
    """异常的影响链。全部字段来自 detect_task 的输出，不做二次计算。"""
    anomalyId: str
    type: str
    typeLabel: str
    typeNote: str = ""
    detectorRule: Optional[str] = None
    detectorRuleLabel: Optional[str] = None
    severity: Optional[str] = None
    confidence: Optional[float] = None
    rootEventId: Optional[str] = None       # 可观测故障起点
    rootNodeId: Optional[str] = None
    latentRootNote: str = ""                # 潜在注入根因的说明
    affectedEvents: List[str] = Field(default_factory=list)
    affectedNodes: List[str] = Field(default_factory=list)
    affectedHypotheses: List[str] = Field(default_factory=list)
    pathToConclusion: List[str] = Field(default_factory=list)
    graphPath: List[str] = Field(default_factory=list)
    reachesReport: bool = False
    evidence: List[Dict[str, Any]] = Field(default_factory=list)


class IntegrityView(BaseModel):
    ok: bool
    nEvents: int
    tipHash: Optional[str] = None
    expectedTipHash: Optional[str] = None
    nIssues: int = 0
    issues: List[Any] = Field(default_factory=list)


class ReplayCheck(BaseModel):
    step: int
    deltaId: str
    beforeHash: str
    expectedAfterHash: str
    replayedHash: Optional[str] = None
    ok: Optional[bool] = None


class ReplayView(BaseModel):
    nDeltas: int
    allOk: bool
    applicable: bool = True
    checks: List[ReplayCheck] = Field(default_factory=list)


class GroundingView(BaseModel):
    evidenceTotal: int = 0
    evidenceSourceBound: int = 0
    evidenceSourceBoundRate: Optional[float] = None
    nNodesBound: int = 0
    nRetrievalRecords: int = 0
    bySourceClass: Dict[str, int] = Field(default_factory=dict)
    bySourceClassLabel: Dict[str, int] = Field(default_factory=dict)
    bySemanticKind: Dict[str, int] = Field(default_factory=dict)
    bySemanticKindLabel: Dict[str, int] = Field(default_factory=dict)


class AuditView(BaseModel):
    caseId: str
    # False = **不适用**（手写展示轨迹），不是「跑出来是空的」。
    # 这个区别必须在协议里，否则前端只能拿 `integrity.ok == False` 去猜 ——
    # 而那个 False 表达的是「校验失败」，是完全不同的一句话。
    applicable: bool = True
    notApplicableReason: str = ""
    integrity: IntegrityView
    replay: ReplayView
    grounding: GroundingView
    tierCounts: Dict[str, int]
    tierCountsLabel: Dict[str, int]
    tiers: List[TierDecisionView]
    anomalies: List[AnomalyPath]


# ------------------------------------------------------------ 账本
class LedgerRow(BaseModel):
    callId: str
    stage: Optional[str] = None
    stageLabel: Optional[str] = None
    actorId: Optional[str] = None
    actorRole: Optional[str] = None
    actorRoleLabel: Optional[str] = None
    modelId: Optional[str] = None
    binding: Optional[str] = None
    bindingLabel: Optional[str] = None
    parseStatus: Optional[str] = None
    parseStatusLabel: Optional[str] = None
    committedToState: bool = False
    commitTarget: Optional[str] = None
    commitTargetLabel: Optional[str] = None
    committedEventIds: List[str] = Field(default_factory=list)
    discardReason: Optional[str] = None
    discardReasonLabel: Optional[str] = None
    promptHash: Optional[str] = None
    responseHash: Optional[str] = None
    stepId: Optional[int] = None
    timestamp: Optional[str] = None
    # 前端需要一眼看出「哪些调用发生过但没进入正式推理状态」
    isUncommitted: bool = False


class LedgerView(BaseModel):
    caseId: str
    # False = **没有账本**（手写展示轨迹）。与 `nCalls == 0` 是两件事：
    # 后者在界面上会读成「这次运行没调用过模型」，而真相是「没有账本可读」。
    applicable: bool = True
    notApplicableReason: str = ""
    nCalls: int
    nAccepted: int
    nDiscarded: int
    nUncommitted: int
    rows: List[LedgerRow]


# ------------------------------------------------------------ 经验
class OutcomeView(BaseModel):
    verdict: Optional[str] = None
    verdictLabel: Optional[str] = None
    verdictDesc: Optional[str] = None
    judgeScores: List[Optional[int]] = Field(default_factory=list)
    unanimous: Optional[bool] = None
    prediction: Optional[str] = None
    referenceSha256: Optional[str] = None


class GateView(BaseModel):
    eligibility: Optional[str] = None
    eligibilityLabel: Optional[str] = None
    eligibilityDesc: Optional[str] = None
    reasons: List[str] = Field(default_factory=list)
    nAnomalies: int = 0
    nUsedP3: int = 0
    nUsedP1: int = 0


class ExperienceCard(BaseModel):
    """**允许作为长期经验保存的抽象内容。**

    这里刻意不含诊断结论、患者文本或参考答案。`forbidden` 字段记录这次
    检查做了什么，让「为什么不能直接存病历」在界面上是可见的而不是承诺。
    """
    memoryId: Optional[str] = None
    partition: str
    partitionLabel: str
    partitionDesc: str
    title: str
    failureMode: Optional[str] = None
    failureModeLabel: Optional[str] = None   # 失败模式的中文名
    avoidanceRule: Optional[str] = None
    suggestedChecks: List[str] = Field(default_factory=list)
    rendered: Optional[str] = None       # memory_formatter 渲染出的可读块
    renderedEn: Optional[str] = None     # 英文原文
    sourceTraceId: Optional[str] = None
    auditSummaryHash: Optional[str] = None
    outcomeVerdict: Optional[str] = None
    extractorVersion: Optional[str] = None


class ForbiddenCheck(BaseModel):
    checked: bool
    containsGold: bool = False
    containsPatientText: bool = False
    containsReferenceDiagnosis: bool = False
    note: str = ""


class ExperienceView(BaseModel):
    caseId: str
    # False = **没有判题与门控**（手写展示轨迹）。没有它的话，界面只能把
    # 空值渲染成「待判定」—— 那是在说「还在等结果」，而真相是根本没有结果
    # 会来。
    applicable: bool = True
    notApplicableReason: str = ""
    outcome: OutcomeView
    gate: GateView
    card: Optional[ExperienceCard] = None
    forbidden: ForbiddenCheck
    headline: str = ""                  # 「结果正确 ≠ 过程可信」这类结论句


# ------------------------------------------------------------ 演示脚本
class DemoStep(BaseModel):
    index: int
    deltaId: str
    eventId: str
    title: str
    narrative: str
    highlights: List[str] = Field(default_factory=list)
    addedNodes: List[str] = Field(default_factory=list)
    isKeyStep: bool = False


class CaseInput(BaseModel):
    """病例输入摘要。左栏顶部那张琥珀色卡片显示它。

    取自 bindings 里 `source_class == INPUT_OBSERVATION` 或 `node_type ==
    Signal` 的节点 —— 与 `evidence_auditor` 判定「病例输入」用的是同一个
    口径。另立一套口径会让「输入是什么」在两个地方给出不同答案。
    """
    symptom: str = ""
    symptomEn: str = ""
    evidence: List[str] = Field(default_factory=list)
    evidenceEn: List[str] = Field(default_factory=list)


class DemoScript(BaseModel):
    caseId: str
    steps: List[DemoStep]
    epilogue: str = ""
    caseInput: CaseInput = Field(default_factory=CaseInput)


# ------------------------------------------------------------ 知识库目录
class KnowledgeDistribution(BaseModel):
    label: str
    count: int


class KnowledgeSampleField(BaseModel):
    label: str
    value: str


class KnowledgeSample(BaseModel):
    title: str
    category: str
    fields: List[KnowledgeSampleField] = Field(default_factory=list)


class KnowledgeBaseEntry(BaseModel):
    id: str
    name: str
    recordCount: int
    recordCountLabel: str
    taskCount: Optional[int] = None
    samples: List[KnowledgeSample] = Field(default_factory=list)
    distributions: List[KnowledgeDistribution] = Field(default_factory=list)


class KnowledgeBasesView(BaseModel):
    privacyNote: str
    knowledgeBases: List[KnowledgeBaseEntry]


BindingIndex.model_rebuild()
