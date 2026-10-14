"""经验中心：结果验证 + 经验门控 + 抽象经验卡。

这一页要讲的是全系统最重要的一句话：**结果正确 ≠ 过程可信**。所以 outcome
与 gate 必须并列展示 —— 单独看 `verified_success` 会得出「这次跑得很好」，
单独看 `failure_experience` 会得出「这次跑砸了」，两者都对，两者都不完整。

**隐私边界是本模块的一部分，不是注释。** `forbidden` 字段记录这次检查
到底比对了什么。这里必须诚实到有点难看的程度：

* 能比对的：`gate_candidates.jsonl` 里的 `task_retrieval_key`（含患者原文）、
  `outcome_results.csv` 里的 `prediction`（含系统自己的答案）。
* **不能比对的：参考答案原文。** 本部署里它不存在，只有它的 sha256。
  所以「经验卡不含参考答案」这句话在本部署中**无法被完整验证** ——
  界面必须这么说，而不是打一个它验证不了的勾。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from .. import i18n
from ..zh import translate, translate_doc
from ..schemas import (ExperienceCard, ExperienceView, ForbiddenCheck,
                       GateView, OutcomeView)

# `A1/evidence_tier_p3_used@evt-case-85-00016`
_REASON = re.compile(r"^(?P<type>A\d)/(?P<rule>[^@]+)@(?P<event>\S+)$")


def _split_reasons(raw: str) -> List[str]:
    """`gate_reasons` 是 CSV 里的一整句，拆成可读的条目。

    原串形如：
        localized anomaly root(s): A1/evidence_tier_p3_used@evt-…, A1/…@evt-…
    直接展示这串英文等于把审计的内部记法丢给评审；
    拆成「A1 无来源证据 · P3 节点被用于推理」才是在解释它。
    """
    if not raw:
        return []
    body = raw.split(":", 1)[1] if raw.lower().startswith("localized") else raw
    out = []
    for part in body.split(","):
        part = part.strip()
        if not part:
            continue
        m = _REASON.match(part)
        if not m:
            out.append(part)
            continue
        atype, rule, event = m.group("type"), m.group("rule"), m.group("event")
        out.append(f"{i18n.label('anomalyType', atype)} · "
                   f"{i18n.label('detectorRule', rule)} · {event}")
    return out


def _outcome(row: Dict[str, str]) -> OutcomeView:
    verdict = row.get("outcome_verdict") or None
    scores: List[Optional[int]] = []
    for s in (row.get("judge_scores") or "").split("|"):
        s = s.strip()
        if s:
            try:
                scores.append(int(s))
            except ValueError:
                scores.append(None)
    unanimous = row.get("judge_unanimous")
    return OutcomeView(
        verdict=verdict,
        verdictLabel=i18n.label("outcome", verdict),
        verdictDesc=i18n.label("outcomeDesc", verdict),
        judgeScores=scores,
        unanimous=(unanimous == "True") if unanimous else None,
        prediction=row.get("prediction") or None,
        referenceSha256=row.get("reference_sha256") or None,
    )


def _gate(row: Dict[str, str], n_anomalies: int) -> GateView:
    elig = row.get("gate_eligibility") or None
    return GateView(
        eligibility=elig,
        eligibilityLabel=i18n.label("partition", elig),
        eligibilityDesc=i18n.label("partitionDesc", elig),
        reasons=_split_reasons(row.get("gate_reasons", "")),
        nAnomalies=int(row.get("n_anomalies") or n_anomalies or 0),
        nUsedP3=int(row.get("n_used_p3") or 0),
        nUsedP1=int(row.get("n_used_p1") or 0),
    )


def _card(cand: Dict[str, Any]) -> Optional[ExperienceCard]:
    if not cand:
        return None
    payload = cand.get("payload") or {}
    kind = payload.get("type", "")
    # 标题取 payload 的类型，而不是 memory_id —— 后者对本系统之外的人
    # 没有任何含义，而「已定位的失败经验」是要立刻读懂的。
    title = {"validated_failure": "可定位的失败模式",
             "validated_success": "已验证的成功策略"}.get(kind, kind or "经验卡")

    rendered_en = None
    try:
        from traceevo.evolution.memory_formatter import render
        # 只传抽象半段：render 读的是 payload，不含患者原文。
        rendered_en = render([{"payload": payload}]) or None
    except Exception:
        rendered_en = None

    # 这张卡是「可以离开系统、进入长期记忆」的内容，读它的人不一定是
    # 开发者，所以正文必须中文。`renderedEn` 仍然下发 —— 它是要被注入
    # 提示词的那段文本，核对注入内容时看的是它。
    rendered = translate_doc(rendered_en) if rendered_en else None

    return ExperienceCard(
        memoryId=cand.get("memory_id") or None,
        partition=cand.get("gate_eligibility") or "",
        partitionLabel=i18n.label("partition", cand.get("gate_eligibility")),
        partitionDesc=i18n.label("partitionDesc",
                                 cand.get("gate_eligibility")),
        title=title,
        failureMode=payload.get("failure_pattern") or None,
        failureModeLabel=i18n.label("failurePattern",
                                    payload.get("failure_pattern")),
        avoidanceRule=(" ".join(translate(a) for a in payload.get("avoid") or [])
                       or None),
        suggestedChecks=[translate(c)
                         for c in payload.get("recommended_check") or []],
        rendered=rendered,
        renderedEn=rendered_en,
        sourceTraceId=cand.get("source_trace_id") or None,
        auditSummaryHash=cand.get("audit_summary_hash") or None,
        outcomeVerdict=cand.get("outcome_verdict") or None,
        extractorVersion=cand.get("extractor_version") or None,
    )


def _forbidden(cand: Dict[str, Any], outcome: OutcomeView,
               gate: GateView) -> ForbiddenCheck:
    """如实报告这次边界检查比对了什么、没比对什么。

    这里不返回一个笼统的「通过」。它分别回答三个问题，并且对答不上来的
    那个（参考答案）明确说「本部署不可读」—— 一个没被验证的命题显示为
    已通过，比显示为未知危险得多。
    """
    if not cand:
        return ForbiddenCheck(checked=False, note="该病例没有经验卡，未做检查")

    payload = cand.get("payload") or {}
    blob = " ".join(str(v) for v in payload.values()).lower()

    key = (cand.get("task_retrieval_key") or "").lower()
    # 患者原文在 task_retrieval_key 里。取其中较长的片段做包含判断，
    # 避免把 "the" 这类词当成命中。
    has_patient = any(
        seg.strip() and len(seg.strip()) >= 24 and seg.strip().lower() in blob
        for seg in key.replace("\n", " ").split("."))

    pred = (outcome.prediction or "").lower()
    has_pred = bool(pred) and len(pred) >= 8 and pred in blob

    gold_note = ("参考答案在本部署中不可读（仅存哈希 "
                 f"{(outcome.referenceSha256 or '')[:12]}…），"
                 "「不含参考答案」无法在此验证")
    return ForbiddenCheck(
        checked=True,
        containsGold=False,          # 无从比对，恒为 False 是诚实的：没发现 ≠ 已验证
        containsPatientText=has_patient,
        containsReferenceDiagnosis=False,
        note=("已比对：患者原文、系统自身预测。"
              + ("命中患者原文！" if has_patient else "两者均未出现在简介中。")
              + f" 未比对：{gold_note}"),
    )


def _headline(outcome: OutcomeView, gate: GateView) -> str:
    if (outcome.verdict == "verified_success"
            and gate.eligibility == "failure_experience"):
        return i18n.UI["outcome_correct_process_risky"]
    if outcome.verdict == "verified_success" and gate.eligibility == "eligible":
        return "结果正确且过程可信，可作为可复用经验保存"
    if outcome.verdict == "verified_incorrect" and gate.eligibility == "reject":
        return "结果错误且未通过硬门，不进经验库"
    return i18n.label("outcome", outcome.verdict)


def _not_applicable(case_id: str, why: str) -> ExperienceView:
    """没有判题/门控产物时的那一版。

    三块内容全部留空，`headline` 说明原因。**不编默认值**：`verdict=None`
    在界面上是「未判定」，而填个 `"unverified"` 会被读成「判过、没通过」。

    这一段文字同时进 `notApplicableReason`（界面据此提前返回）与
    `headline`（JSON 单独被读时的说明），两处口径一致。
    """
    return ExperienceView(
        caseId=case_id,
        applicable=False,
        notApplicableReason=why,
        outcome=OutcomeView(),
        gate=GateView(),
        card=None,
        # checked=False：**没有检查过**，不是「检查过、没发现」。
        forbidden=ForbiddenCheck(checked=False, note=why),
        headline=why,
    )


def build(bundle: Any, gate_row_fn, outcome_row_fn, candidate_fn) -> ExperienceView:
    from ..config import case_task
    # 判题与门控的产物只对**跑过的病例**存在（它们在阶段交付目录里，按病例
    # 区间分文件）。展示轨迹两者都没有，而且它的病例名不以数字结尾 ——
    # 硬解析会抛 ValueError 变成 500，让「不适用」看起来像「系统坏了」。
    try:
        num = int(bundle.task.split("-")[-1])
    except ValueError:
        return _not_applicable(
            case_task(bundle.task),
            "展示轨迹：没有判题与门控 —— 它是手写的展示数据，"
            "没有经过参考答案比对，也没有走过经验门控")
    outcome = _outcome(outcome_row_fn(num))
    cand = candidate_fn(num)
    gate = _gate(gate_row_fn(num), len(bundle.injections))
    return ExperienceView(
        caseId=case_task(bundle.task),
        outcome=outcome,
        gate=gate,
        card=_card(cand),
        forbidden=_forbidden(cand, outcome, gate),
        headline=_headline(outcome, gate),
    )
