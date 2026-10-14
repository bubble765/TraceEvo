"""中文词表：系统里每一个业务标签的唯一真源。

前端**不硬编码任何业务文案**。所有标签由 `/api/i18n` 下发，前端只负责渲染。
这样做的理由不是省事，而是避免同一份词表在两处维护后各自漂移 —— 当
「P3 无来源断言」在一处叫「无依据」，另一处叫「无来源」，评审看到的
就不再是同一个概念。

词表的取值全部来自 `traceevo/` 里已冻结的常量，不是另写一套：本模块
导入它们并对每个值给出中文。若某个值没有对应中文，`label()` 返回原值
而不是空串 —— 一个显示为 `A6` 的未知异常类型是可以被发现的，一个显示
为空白的同一异常则不会。

词表按「域」组织，域的名字对应数据协议里的字段名。
"""
from __future__ import annotations

from typing import Any, Dict

# ------------------------------------------------------------------ 来源等级
# 取值来自 traceevo.audit.evidence_auditor.TIERS
TIER = {
    "P0_DIRECT_GROUNDED": "P0 直接有据",
    "P1_SOFT_GROUNDED": "P1 软支撑",
    "P2_DERIVED": "P2 合法推导",
    "P3_UNSUPPORTED_ASSERTION": "P3 无来源断言",
}
TIER_SHORT = {"P0_DIRECT_GROUNDED": "P0", "P1_SOFT_GROUNDED": "P1",
              "P2_DERIVED": "P2", "P3_UNSUPPORTED_ASSERTION": "P3"}
TIER_DESC = {
    "P0_DIRECT_GROUNDED": "来自病例输入或明确检索记录的直接支撑",
    "P1_SOFT_GROUNDED": "存在候选记录，但只能软匹配",
    "P2_DERIVED": "缺席声明、专家综合、领域推导等可追溯的推理，非外部事实",
    "P3_UNSUPPORTED_ASSERTION": "事实性证据无法绑定到输入或记录，"
                                "也不属于合法推导，且参与支持/反驳路径",
}
TIER_ORDER = ["P0_DIRECT_GROUNDED", "P1_SOFT_GROUNDED", "P2_DERIVED",
              "P3_UNSUPPORTED_ASSERTION"]

# ------------------------------------------------------------ 审计判定标签
# 取值来自 evidence_auditor.LBL_*
AUDIT_LABEL = {
    "ok": "来源完整",
    "soft_grounded_review": "软支撑待复核",
    "unsupported_unused": "无来源但未参与推理",
    "unsupported_assertion": "无来源断言",
}

# ------------------------------------------------------------------ 推导来源
# 取值来自 evidence_auditor 的 CONTENT_LINEAGE / SELF_DECLARED 两个集合与
# `none`。这三类含义不同，界面必须分开显示：
#   内容有血脉    lineage 完整，可追到输入或记录
#   自述         执行血脉完整，但内容没有前件（是智能体自己的断言）
#   无           两者都没有
DERIVATION = {
    "case_input": "病例输入",
    "record_quote": "检索记录原文引用",
    "record_verbatim": "检索记录逐字引用",
    "record_paraphrase": "检索记录复述",
    "record_bound": "绑定到检索记录",
    "expert_synthesis": "专家综合分析",
    "hypothesis": "诊断假设",
    "absence_claim": "缺席声明",
    "recommendation": "检查建议",
    "domain_knowledge": "领域知识",
    "agent_inference": "智能体推断",
    "input_inference": "输入再推导",
    "none": "无来源",
}
DERIVATION_KIND = {
    "case_input": "lineage", "record_quote": "lineage",
    "record_verbatim": "lineage", "record_paraphrase": "lineage",
    "record_bound": "lineage", "expert_synthesis": "lineage",
    "hypothesis": "self_declared", "absence_claim": "self_declared",
    "recommendation": "self_declared", "domain_knowledge": "self_declared",
    "agent_inference": "self_declared", "input_inference": "self_declared",
    "none": "absent",
}
DERIVATION_KIND_LABEL = {
    "lineage": "内容可追溯到输入或检索记录",
    "self_declared": "执行血脉完整，但内容无前件（智能体自述）",
    "absent": "既无内容血脉也无自述依据",
}

# -------------------------------------------------------------- 语义类型
SEMANTIC_KIND = {
    "clinical_evidence": "临床证据",
    "domain_knowledge": "领域知识",
    "agent_inference": "智能体推断",
    "absence_claim": "缺席声明",
    "recommendation": "建议",
    "hypothesis": "假设",
    "conclusion": "结论",
}

# -------------------------------------------------------------- 来源类别
SOURCE_CLASS = {
    "INPUT_OBSERVATION": "病例输入",
    "RETRIEVED_RECORD": "检索记录",
    "AGENT_GENERATED": "智能体生成",
    "SYSTEM_GENERATED": "系统生成",
    "UNKNOWN": "未知",
}

# ------------------------------------------------------------ 图节点类型
NODE_TYPE = {
    "Signal": "起始信号",
    "Evidence": "医学证据",
    "Hypothesis": "诊断假设",
    "Conclusion": "最终结论",
    # 知识会诊拉进图的 RAG 条目。参考项目把它画成紫色菱形、圆内字母 K，
    # 与四类推理节点并列 —— 它不是「证据」：证据来自本次诊疗的检查，
    # 知识来自库，混成一类会让「这条支持边有据可查吗」无法回答。
    "Knowledge": "知识库 (RAG)",
    "Agent": "智能体",
    "Tool": "工具",
    "Record": "检索记录",
    "Action": "执行动作",
}

# -------------------------------------------------------------- 推理关系
RELATION = {
    "support": "支持",
    "refute": "反驳",
    "refines": "细化",
    "propagate": "传播",
}

# ------------------------------------------------------ 执行溯源图边关系
PROV_EDGE = {
    "INVOKE": "调用",
    "GENERATE": "生成",
    "DEPEND": "依赖",
    "READ": "读取",
    "SUPPORT": "支持",
    "REFUTE": "反驳",
    "PROPAGATE": "传播",
}

# -------------------------------------------------------------- 事件类型
# 取值来自 traceevo.audit.event_schema.EVENT_TYPES
EVENT_TYPE = {
    "PLAN": "规划",
    "AGENT_CALL": "专家调用",
    "TOOL_CALL": "工具调用",
    "EVIDENCE_READ": "证据读取",
    "ANALYSIS": "专家分析",
    "GRAPH_UPDATE": "推理图更新",
    "BACKTRACK": "回溯",
    "DRILL_DOWN": "深入分析",
    "FINAL_REPORT": "最终报告",
}
# 当前溯源图节点的 kind 是事件类型的小写形式。旧版产物兼容读取时，
# 还可能出现 Agent / Tool / Record / Action 等合成节点。
PROV_KIND = {k.lower(): v for k, v in EVENT_TYPE.items()}
PROV_KIND.update({"agent": "智能体", "tool": "工具", "record": "检索记录",
                  "action": "执行动作"})

# ------------------------------------------------------------------ 状态
STATUS = {"success": "成功", "degraded": "降级", "failed": "失败"}
REASON_CODE = {
    "NONE": "无",
    "BUDGET_EXHAUSTED": "预算耗尽",
    "INSUFFICIENT_INFORMATION_FALLBACK": "信息不足回退",
    "INVALID_EXPERT_DISPATCH": "专家分派无效",
    "TOOL_ERROR": "工具错误",
    "LLM_ERROR": "模型错误",
    "PARSER_ERROR": "解析错误",
    "TRACE_INTEGRITY_ERROR": "轨迹完整性错误",
}

# ------------------------------------------------------------------ 异常类
# A0 与 A4 的措辞是有意的：A0 在本流水线里从不自然发生，A4 已被降级为
# 解析边界案例，两者都不进 headline。界面若把它们与 A1/A2/A3/A5 并列成
# 「同类异常」，就等于把 S5A 已经做出的区分又抹掉了。
ANOMALY_TYPE = {
    "A0": "执行降级",
    "A1": "无来源证据",
    "A2": "证据错误绑定或记录篡改",
    "A3": "非法能力调用",
    "A4": "解析边界",
    "A5": "轨迹完整性异常",
}
ANOMALY_NOTE = {
    "A0": "本流水线从不产生 degraded/failed 状态，该类不进入统计口径",
    "A1": "事实性证据无法绑定到输入或检索记录",
    "A2": "节点绑定的记录无法支撑该绑定，或记录原文被改动",
    "A3": "请求调用了专家名单之外的能力",
    "A4": "解析边界案例，不计入语义传播 F1",
    "A5": "事件日志的哈希链或重放校验不通过",
}

# ------------------------------------------------------------ 异常检测规则
DETECTOR_RULE = {
    "evidence_tier_p3_used": "P3 节点被用于推理",
    "record_hash_mismatch": "记录哈希与原文不符",
    "binding_not_reproducible_against_bound_record": "绑定无法对所指记录复现",
    "ungrounded_analysis_quantity": "分析中的数值无可查来源",
    "ungrounded_analysis_finding": "分析中的发现无可查来源",
    "degraded_status": "执行状态降级",
    "silent_fallback_text": "静默回退文本",
    "hash_chain_broken": "哈希链断裂",
    "dag_cycle": "依赖图成环",
    "dag_orphan": "依赖图存在孤立节点",
}

# ------------------------------------------------------------ 经验分区
# 取值来自 traceevo.evolution.experience_gate
PARTITION = {
    "eligible": "可信成功经验",
    "failure_experience": "可定位失败经验",
    "manual_review": "人工复核",
    "reject": "拒绝",
}
PARTITION_DESC = {
    "eligible": "结果验证通过，且过程审计无异常，可作为可复用成功经验",
    "failure_experience": "过程带有可定位的异常，固化为失败模式而非成功经验",
    "manual_review": "存在未解决的决策路径风险，需人工复核后决定",
    "reject": "未通过硬门（完整性、调用链或语义轨迹）",
}

# ------------------------------------------------------------ 结果验证
OUTCOME = {
    "verified_success": "结果验证正确",
    "verified_incorrect": "结果验证错误",
    "uncertain": "结果不确定",
}
OUTCOME_DESC = {
    "verified_success": "三次独立评判一致给出满分",
    "verified_incorrect": "三次独立评判一致给出零分",
    "uncertain": "三次评判不一致，或一致给出部分分",
}

# ------------------------------------------------------------ 失败模式
# 取值来自 `traceevo.evolution.experience` 的 failure_pattern。
# 经验卡上显示这个代码是无意义的 —— 「UNSUPPORTED_EVIDENCE」对人不是信息。
FAILURE_PATTERN = {
    "UNSUPPORTED_EVIDENCE": "无来源证据被当作临床证据",
    "ANOMALOUS_EXECUTION": "执行过程异常",
    "UNSUPPORTED_ASSERTION": "无来源断言",
    "RECORD_TAMPERING": "检索记录被篡改",
    "ILLEGAL_CAPABILITY": "越权能力调用",
    "TRACE_INTEGRITY": "轨迹完整性异常",
    "PARSER_BOUNDARY": "解析边界",
}

# ------------------------------------------------------------ 门控基线规则
GATE_RULE = {
    "B0_Naive": "全部接纳",
    "B1_OutcomeOnly": "仅看结果",
    "B2_AuditOnly": "仅看过程审计",
    "B3_IntegrityOnly": "仅看哈希链",
    "TraceEvo": "结果与过程联合",
}

# ------------------------------------------------------------ 模型调用账本
CALL_STAGE = {
    "GRAPH_UPDATE": "推理图更新",
    "OTHER": "其他",
    "CENTRAL_PLAN": "中央智能体规划",
    "EXPERT_DECISION": "专家决策",
    "EXPERT_CONTENT": "专家内容生成",
    "EVIDENCE_RETRIEVAL": "证据检索",
    "REPORT": "报告生成",
}
PARSE_STATUS = {"accepted": "已接受", "discarded": "已丢弃"}
COMMIT_TARGET = {"EVENT": "事件", "GRAPH_BELIEF": "信念图", "": "未提交"}
LEDGER_BINDING = {"text": "文本绑定", "positional": "位置绑定",
                  "": "未绑定"}
DISCARD_REASON = {
    "NONE": "无",
    "DECISION_CONTENT_MISMATCH": "决策与内容不匹配",
}

# ------------------------------------------------------------ 角色
ACTOR_ROLE = {"central": "中央智能体", "expert": "专家智能体"}

# 智能体/专家名 -> 中文。取值口径与 `医疗溯因智能体/gos_repo/viz/
# trace_to_story.py` 的 ACTOR_MAP 对齐，两个项目展示同一套流水线时
# 人名必须一致，否则会被读成两批不同的智能体。
AGENT_NAME = {
    "Primary_Physician": "主诊医师",
    "Central_Agent": "中央智能体",
    "Laboratory_Physician": "检验医师",
    "Radiologist": "影像医师",
    "Pathologist": "病理医师",
    "Medical_Knowledge_Consultant": "知识检索员",
    "Patient": "患者主诉",
    "SRE_Commander": "运维指挥官",
    "Linux_Agent": "Linux 运维",
    "NetworkOperator": "网络运维",
    "DatabaseOperator": "数据库运维",
}

# 头像。参考项目用 emoji 让人一眼分辨「这一步是谁做的」——
# 在只有 200px 宽的节点卡上，一个图标比一个四字姓名更快被认出来。
AGENT_ICON = {
    "Primary_Physician": "🧑⚕️", "Central_Agent": "🎛️",
    "Laboratory_Physician": "🧪", "Radiologist": "🩻",
    "Pathologist": "🔬", "Medical_Knowledge_Consultant": "📚",
    "Patient": "🗣️", "SRE_Commander": "🎖️", "Linux_Agent": "🐧",
    "NetworkOperator": "🌐", "DatabaseOperator": "🗄️",
}


# 工具名 -> 中文。GoS 的 `tool_or_model` 字段里既有工具名也有专家名
# （检索由专家发起时那一格记的是专家），所以这张表和 AGENT_NAME 要一起查。
TOOL_NAME = {
    "Evidence_Text": "证据检索",
    "Exam_Lookup": "检查查询",
    "Knowledge_RAG": "知识检索",
    "Report_Writer": "报告撰写",
}

# 工具图标。与专家头像同一用途：在窄卡片上一眼分辨「这一步在干什么」。
TOOL_ICON = {
    "Evidence_Text": "📄", "Exam_Lookup": "🔎", "Knowledge_RAG": "📚",
    "Report_Writer": "📝",
}


def entity_label(name: Any) -> str:
    """智能体或工具名 -> 中文。

    先查专家表再查工具表：两表有交集时（专家的名字被当成工具调用）应当
    显示专家名 —— `Radiologist` 是「影像医师」，即使它出现在 tool 字段里。
    两张表都没命中就原样返回：一个显示为 `Foo_Bar` 的未知实体在界面上是
    可以被发现的，一个空白格不是。
    """
    if not name:
        return ""
    s = str(name)
    return AGENT_NAME.get(s) or TOOL_NAME.get(s) or s


def entity_icon(name: Any) -> str:
    s = str(name or "")
    return AGENT_ICON.get(s) or TOOL_ICON.get(s) or "🩺"


def agent_label(name: Any) -> str:
    return entity_label(name)


def agent_icon(name: Any) -> str:
    return entity_icon(name)

# ------------------------------------------------------------ 界面固定文案
UI = {
    "app_title": "TraceEvo 双图审计溯源",
    "app_subtitle": "面向医学多智能体协同推理的双图审计溯源与可信经验固化",
    "graph_reasoning": "医学因果推理图",
    "graph_provenance": "协作执行溯源图",
    "panel_step": "当前步骤详情",
    "panel_audit": "审计信息",
    "panel_ledger": "模型调用账本",
    "action_focus_path": "仅显示关联路径",
    "action_full_graph": "回到完整图",
    "action_locate_source": "定位来源",
    "action_view_evidence": "查看原始证据",
    "legend_title": "证据来源等级",
    "timeline_title": "执行时间轴",
    "unreached": "未参与最终推理",
    "reached": "参与最终推理",
    "replay_ok": "重放校验通过",
    "replay_bad": "重放校验未通过",
    "integrity_ok": "哈希链完整",
    "integrity_bad": "哈希链异常",
    "outcome_correct_process_risky": "结果正确 ≠ 过程可信",
    "potential_injection_root": "潜在注入根因",
    "observable_fault_seam": "可观测故障起点",
    "abstract_only": "可长期保存的抽象内容",
    "full_trace_audit_only": "为审计永久保存的完整轨迹",
    "no_data": "暂无数据",
}

# ------------------------------------------------------------------ 查询接口
DOMAINS: Dict[str, Dict[str, Any]] = {
    "tier": TIER,
    "tierShort": TIER_SHORT,
    "tierDesc": TIER_DESC,
    "tierOrder": TIER_ORDER,
    "auditLabel": AUDIT_LABEL,
    "derivation": DERIVATION,
    "derivationKind": DERIVATION_KIND,
    "derivationKindLabel": DERIVATION_KIND_LABEL,
    "semanticKind": SEMANTIC_KIND,
    "sourceClass": SOURCE_CLASS,
    "nodeType": NODE_TYPE,
    "relation": RELATION,
    "provEdge": PROV_EDGE,
    "eventType": EVENT_TYPE,
    "provKind": PROV_KIND,
    "status": STATUS,
    "reasonCode": REASON_CODE,
    "anomalyType": ANOMALY_TYPE,
    "anomalyNote": ANOMALY_NOTE,
    "detectorRule": DETECTOR_RULE,
    "failurePattern": FAILURE_PATTERN,
    "partition": PARTITION,
    "partitionDesc": PARTITION_DESC,
    "outcome": OUTCOME,
    "outcomeDesc": OUTCOME_DESC,
    "gateRule": GATE_RULE,
    "callStage": CALL_STAGE,
    "parseStatus": PARSE_STATUS,
    "commitTarget": COMMIT_TARGET,
    "ledgerBinding": LEDGER_BINDING,
    "discardReason": DISCARD_REASON,
    "actorRole": ACTOR_ROLE,
    "agentName": AGENT_NAME,
    "agentIcon": AGENT_ICON,
    "toolName": TOOL_NAME,
    "toolIcon": TOOL_ICON,
    "ui": UI,
}


def label(domain: str, value: Any) -> str:
    """取中文标签。未知值原样返回。

    返回原值而不是空串或「未知」是有意的：`A6` 出现在界面上是一个可以
    被报告的缺陷，一个空白格不是。前一个能被发现，后一个会被忽略。
    """
    if value is None or value == "":
        return ""
    table = DOMAINS.get(domain, {})
    return table.get(str(value), str(value))


def vocabulary() -> Dict[str, Any]:
    """整份词表，供 `/api/i18n` 下发。"""
    return DOMAINS
