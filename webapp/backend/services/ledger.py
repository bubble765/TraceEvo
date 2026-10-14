"""模型调用账本。

账本回答的是一个图回答不了的问题：**哪些调用发生过，但没有进入正式推理
状态**。图只显示被采纳的东西 —— 一次被丢弃的解析、一次没绑定到任何图
元素的输出，在推理图上不留痕迹。如果界面只画图，评审看到的「过程」就是
一份已经清理过的过程。

所以 `isUncommitted` 是本模块存在的理由，其余字段是上下文。注意它
**不等于错误**：检索阶段的调用本来就不提交到图元素，这是设计如此。
界面上这一点必须可分辨，否则会把正常流程显示成一片异常。

账本的落盘字段里没有 io 详情（只有 hashes），本模块也不去补造 ——
`committedEventIds` 指向的事件才是内容的载体，界面从事件取内容。
"""
from __future__ import annotations

from typing import Any, List

from .. import i18n
from ..schemas import LedgerRow, LedgerView


def _row(inv: Dict[str, Any]) -> LedgerRow:
    parse_status = inv.get("parse_status") or ""
    committed = bool(inv.get("committed_to_state"))
    commit_target = inv.get("commit_target") or ""
    return LedgerRow(
        callId=inv.get("call_id", ""),
        stage=inv.get("stage") or None,
        stageLabel=i18n.label("callStage", inv.get("stage")),
        actorId=inv.get("actor_id") or None,
        actorRole=inv.get("actor_role") or None,
        actorRoleLabel=i18n.label("actorRole", inv.get("actor_role")),
        modelId=inv.get("model_id") or None,
        binding=inv.get("binding") or None,
        bindingLabel=i18n.label("ledgerBinding", inv.get("binding")),
        parseStatus=parse_status or None,
        parseStatusLabel=i18n.label("parseStatus", parse_status),
        committedToState=committed,
        commitTarget=commit_target or None,
        commitTargetLabel=i18n.label("commitTarget", commit_target),
        committedEventIds=list(inv.get("committed_event_ids") or []),
        discardReason=inv.get("discard_reason") or None,
        discardReasonLabel=i18n.label("discardReason",
                                      inv.get("discard_reason")),
        promptHash=inv.get("prompt_hash") or None,
        responseHash=inv.get("response_hash") or None,
        stepId=inv.get("step_id"),
        timestamp=inv.get("timestamp"),
        # 被采纳、但没有任何图元素承接它。这才是「发生了却没留下痕迹」。
        isUncommitted=(parse_status == "accepted" and not committed),
    )


def build(bundle: Any) -> LedgerView:
    rows: List[LedgerRow] = [_row(i) for i in bundle.ledger]
    n_accepted = sum(1 for r in rows if r.parseStatus == "accepted")
    applicable = getattr(bundle, "kind", "run") == "run"
    return LedgerView(
        caseId=bundle.task,
        applicable=applicable,
        notApplicableReason=("展示轨迹：没有模型调用账本 —— 它的数据是编排的，"
                             "不是某次运行写出来的" if not applicable else ""),
        nCalls=len(rows),
        nAccepted=n_accepted,
        nDiscarded=sum(1 for r in rows if r.parseStatus == "discarded"),
        nUncommitted=sum(1 for r in rows if r.isUncommitted),
        rows=rows,
    )
