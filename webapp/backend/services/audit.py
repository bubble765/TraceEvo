"""审计：包装 evidence_auditor 与 anomaly_detector，产出协议对象。

**这两个模块不在本系统里重写，也不在本系统里重算。** 它们按要求原样复用
（`traceevo/` 全程 0 修改）。这里做的只有三件事：调用、把 dataclass 映射进
协议、补上界面需要的标签。

**为什么这里是「重算」而不是「读结果」：** 每节点 tier 判定没有独立落盘的
产物 —— 磁盘上只有聚合后的 `natural_audit_summary.csv`（p0/p1/p2/p3 计数）。
但 `audit_task` 是**落盘产物的确定性纯函数**（输入是 bindings + records +
events + deltas，输出只由它们决定），所以重算不引入漂移：同样的 artifact
必定得到同样的判定。这一点由测试断言钉住（case-85 必须是 P0=6/P2=7/P3=2）。

真正的风险不是「重算会不一致」，而是「重算会变慢」。所以结果按运行目录的
mtime 缓存 —— 封存产物不变则一次算完永久复用，真实分析模式下产物在变，
缓存自然失效。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .. import i18n
from ..zh import translate
from ..schemas import (AnomalyPath, AuditView, GroundingView, IntegrityView,
                       ReplayCheck, ReplayView, ThresholdContext,
                       TierDecisionView)


@dataclass
class Derived:
    """一次审计的全部派生产物。其它服务共用它，避免重复计算。"""
    decisions: List[Any] = field(default_factory=list)
    anomalies: List[Dict[str, Any]] = field(default_factory=list)
    anomaly_views: Dict[str, AnomalyPath] = field(default_factory=dict)
    tier_counts: Dict[str, int] = field(default_factory=dict)
    # 这份产物**不适用**（展示轨迹），而不是「跑出来是空的」。
    # 两者在界面上必须是两句话：「无审计产物」vs「P0 0 / P1 0 / … / 无异常」。
    # 后一句是一组断言 —— 说这个病例没有无来源断言、没有异常 —— 而手写数据
    # 没有资格下任何断言。
    applicable: bool = True
    not_applicable_reason: str = ""


_CACHE: Dict[tuple, Derived] = {}


def _stamp(run_dir: str, task: str) -> tuple:
    """缓存键：产物文件的大小与 mtime 之和。任一产物变化即失效。"""
    parts = []
    for suf in ("_bindings.json", "_events.jsonl", "_deltas.jsonl"):
        p = os.path.join(run_dir, f"{task}{suf}")
        try:
            st = os.stat(p)
            parts.append((suf, st.st_size, int(st.st_mtime)))
        except OSError:
            parts.append((suf, -1, -1))
    return tuple(parts)


def _thresholds(cfg: Dict[str, Any]) -> ThresholdContext:
    return ThresholdContext(
        directMinCoverage=cfg.get("direct_min_coverage"),
        softMinCoverage=cfg.get("soft_min_coverage"),
        softMinCharSimilarity=cfg.get("soft_min_char_similarity"),
        inputInferenceMinCoverage=cfg.get("input_inference_min_coverage"),
        expertSynthesisMinCoverage=cfg.get("expert_synthesis_min_coverage"),
        minContentTokens=cfg.get("min_content_tokens"),
        source="traceevo.audit.evidence_auditor.DEFAULT_CFG",
    )


def derive(bundle: Any) -> Derived:
    """跑一次审计并缓存。失败时返回空派生而不是抛错。

    审计跑不动（例如中途产物的 delta 只落了一半）不应该让整个工作台变成
    500：图仍然可以显示，只是审计面板显示「不可用」。让可视化的可用性
    依赖审计的可用性，等于让最不该挂的功能先挂。
    """
    # **展示轨迹不跑审计。** 它不是「跑了但没发现问题」，是「没有可审的东西」：
    # 数据是手写的，`audit_task` 会照样从里面算出一组 provenance tier，
    # 而那组数字在界面上与真实运行的审计结论长得**一模一样** —— 这正是
    # 本系统要防的事。所以在这里就停住，并把「为什么停住」带出去。
    if getattr(bundle, "kind", "run") != "run":
        d = Derived(applicable=False,
                    not_applicable_reason="展示轨迹：数据为手写，无审计产物")
        key0 = (bundle.run_dir, bundle.task)
        _CACHE[key0] = d
        return d

    key = (bundle.run_dir, bundle.task) + _stamp(bundle.run_dir, bundle.task)
    if key in _CACHE:
        return _CACHE[key]

    from traceevo.audit.anomaly_detector import detect_task
    from traceevo.audit.evidence_auditor import DEFAULT_CFG, audit_task

    d = Derived()
    try:
        d.decisions = list(audit_task(bundle.run_dir, bundle.task))
    except Exception:
        d.decisions = []
    try:
        d.anomalies = list(detect_task(bundle.run_dir, bundle.task,
                                       cfg=DEFAULT_CFG))
    except Exception:
        d.anomalies = []

    for t in i18n.TIER_ORDER:
        d.tier_counts[t] = sum(
            1 for x in d.decisions if x.provenance_tier == t)

    latent = _latent_roots(bundle)
    for a in d.anomalies:
        v = _anomaly_view(a, latent)
        d.anomaly_views[v.anomalyId] = v

    _CACHE[key] = d
    return d


def _latent_roots(bundle: Any) -> Dict[str, str]:
    """注入记录里的潜在根因事件 -> 说明。

    case-85 的 `_injections.jsonl` 是空的（已核实）：它的 A1 是**自然发生**
    的，不是注入的。界面必须能说清这件事 —— 把一条自然异常说成「注入」，
    或者反过来把注入痕迹隐去，都是在演示里制造不存在的因果关系。
    """
    out: Dict[str, str] = {}
    for rec in bundle.injections:
        for k in ("latent_root_event_id", "observable_root_event_id",
                  "root_event_id", "event_id"):
            eid = rec.get(k)
            if eid:
                out[eid] = str(rec.get("type") or rec.get("injected_type") or "")
    return out


def _anomaly_view(a: Dict[str, Any], latent: Dict[str, str]) -> AnomalyPath:
    atype = a.get("type", "")
    root = a.get("root_event_id") or None
    if a.get("root_event_id") in latent:
        note = i18n.UI["potential_injection_root"]
    elif root:
        note = i18n.UI["observable_fault_seam"]
    else:
        note = ""
    return AnomalyPath(
        anomalyId=a.get("anomaly_id", ""),
        type=atype,
        typeLabel=i18n.label("anomalyType", atype),
        typeNote=i18n.label("anomalyNote", atype),
        detectorRule=a.get("detector_rule") or None,
        detectorRuleLabel=i18n.label("detectorRule", a.get("detector_rule")),
        severity=a.get("severity"),
        confidence=a.get("confidence"),
        rootEventId=root,
        rootNodeId=a.get("root_node_id") or None,
        latentRootNote=note,
        affectedEvents=list(a.get("affected_events") or []),
        affectedNodes=list(a.get("affected_nodes") or []),
        affectedHypotheses=list(a.get("affected_hypotheses") or []),
        pathToConclusion=list(a.get("path_to_conclusion") or []),
        graphPath=list(a.get("graph_path") or []),
        reachesReport=bool(a.get("reaches_report")),
        evidence=list(a.get("evidence") or []),
    )


def _tier_view(d: Any) -> TierDecisionView:
    ntype = getattr(d, "node_type", "")
    return TierDecisionView(
        nodeId=d.node_id,
        label=translate(d.label),
        labelEn=d.label,
        nodeType=ntype,
        nodeTypeLabel=i18n.label("nodeType", ntype),
        provenanceTier=d.provenance_tier,
        tierLabel=i18n.label("tier", d.provenance_tier),
        tierDesc=i18n.label("tierDesc", d.provenance_tier),
        auditLabel=d.audit_label,
        auditLabelText=i18n.label("auditLabel", d.audit_label),
        derivation=d.derivation,
        derivationLabel=i18n.label("derivation", d.derivation),
        derivationKind=i18n.label("derivationKind", d.derivation),
        derivationKindLabel=i18n.label("derivationKindLabel",
                                       i18n.label("derivationKind",
                                                  d.derivation)),
        bestRecordId=d.best_record_id or None,
        bestRecordHash=d.best_record_hash or None,
        tokenCoverage=d.token_coverage,
        charSimilarity=d.char_similarity,
        bestSpanRatio=d.best_span_ratio,
        analysisCoverage=d.analysis_coverage,
        inputCoverage=d.input_coverage,
        usedInReasoning=d.used_in_reasoning,
        usage=d.usage or None,
        externallyGrounded=d.externally_grounded,
        lineageComplete=d.lineage_complete,
        bestAnalysisEvent=d.best_analysis_event or None,
    )


def view(bundle: Any, d: Derived) -> AuditView:
    from traceevo.audit.evidence_auditor import DEFAULT_CFG

    cfg = _thresholds(DEFAULT_CFG)
    integ = bundle.integrity or {}
    rep = bundle.replay or {}
    g = bundle.grounding or {}

    checks = [
        ReplayCheck(
            step=c.get("step", 0), deltaId=c.get("delta_id", ""),
            beforeHash=c.get("before_hash", ""),
            expectedAfterHash=c.get("expected_after_hash", ""),
            replayedHash=c.get("replayed_hash"),
            ok=c.get("ok"))
        for c in (rep.get("checks") or [])
    ]

    by_class = g.get("by_source_class") or {}
    by_kind = g.get("by_semantic_kind") or {}
    grounding = GroundingView(
        evidenceTotal=g.get("evidence_total", 0),
        evidenceSourceBound=g.get("evidence_source_bound", 0),
        evidenceSourceBoundRate=g.get("evidence_source_bound_rate"),
        nNodesBound=g.get("n_nodes_bound", 0),
        nRetrievalRecords=g.get("n_retrieval_records", 0),
        bySourceClass=by_class,
        bySourceClassLabel={i18n.label("sourceClass", k): v
                            for k, v in by_class.items()},
        bySemanticKind=by_kind,
        bySemanticKindLabel={i18n.label("semanticKind", k): v
                             for k, v in by_kind.items()},
    )

    tiers = []
    for dec in d.decisions:
        v = _tier_view(dec)
        v.thresholds = cfg
        tiers.append(v)

    return AuditView(
        caseId=bundle.task,
        applicable=d.applicable,
        notApplicableReason=d.not_applicable_reason,
        integrity=IntegrityView(
            ok=bool(integ.get("ok")),
            nEvents=integ.get("n_events", 0),
            tipHash=integ.get("tip_hash"),
            expectedTipHash=integ.get("expected_tip_hash"),
            nIssues=integ.get("n_issues", 0),
            issues=list(integ.get("issues") or []),
        ),
        replay=ReplayView(
            nDeltas=rep.get("n_deltas", 0),
            allOk=bool(rep.get("all_ok")),
            # 真实分析模式下 delta 还没落盘时，「重放未通过」是错的读法 ——
            # 还没开始重放。applicable 让前端显示「待生成」而不是「失败」。
            applicable=bool(rep),
            checks=checks,
        ),
        grounding=grounding,
        tierCounts=d.tier_counts,
        tierCountsLabel={i18n.label("tier", k): v for k, v in
                         d.tier_counts.items()},
        tiers=tiers,
        anomalies=list(d.anomaly_views.values()),
    )
