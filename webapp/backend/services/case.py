"""门面：一次请求里所有视图共享同一份派生结果。

路由只管 HTTP，装配在这里。这样做的直接好处是**模块之间不可能对不上**：
`binding` 用的 anomalies 与 `audit` 面板显示的是同一批对象，不是两次
分别调出来的结果。审计系统里最隐蔽的 bug 就是面板 A 说 2 条异常、
面板 B 说 3 条，而两边都「没错」。

`Derived`（审计派生物）自带 mtime 缓存，所以一次请求里重复取用是廉价的。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Optional

from ..sources import ArtifactBundle, get_source
from . import audit as audit_svc
from . import binding as binding_svc
from . import delta as delta_svc
from . import ledger as ledger_svc
from . import provenance_graph as prov_svc
from . import reasoning_graph as reason_svc


@dataclass
class Case:
    """一个病例的全部视图。字段按需计算，构造时只做共用的那部分。"""
    bundle: ArtifactBundle
    derived: audit_svc.Derived

    @property
    def task(self) -> str:
        return self.bundle.task

    def reasoning(self, upto: Optional[int] = None):
        return reason_svc.build(self.bundle, self.derived.decisions,
                                self.derived.anomalies, upto=upto)

    def provenance(self, upto: Optional[int] = None):
        # `upto` 与 reasoning() 同义：只保留到该步为止已发生的事件。
        return prov_svc.build(self.bundle, self.derived.anomalies, upto=upto)

    def binding(self):
        return binding_svc.build(
            self.bundle, self.derived.decisions, self.derived.anomalies,
            self.derived.anomaly_views,
            binding_svc.records_by_event(self.bundle))

    def audit(self):
        return audit_svc.view(self.bundle, self.derived)

    def ledger(self):
        return ledger_svc.build(self.bundle)

    def deltas(self):
        return delta_svc.views(self.bundle, self.derived.decisions)

    def demo_script(self):
        return delta_svc.demo_script(self.bundle, self.deltas(),
                                     list(self.derived.anomaly_views.values()),
                                     self.derived.decisions)

    def experience(self):
        from . import experience as exp_svc
        from ..sources.artifact import candidate_row, gate_row, outcome_row
        return exp_svc.build(self.bundle, gate_row, outcome_row, candidate_row)

    def summary(self):
        from .. import i18n
        from ..schemas import CaseSummary
        from ..sources.artifact import gate_row, outcome_row

        integ = self.bundle.integrity or {}
        rep = self.bundle.replay or {}
        atypes = sorted({a.type for a in self.derived.anomaly_views.values()})

        # 判题与门控的产物不在运行目录里，可能缺失（真实分析模式新病例）。
        # 缺失时字段留空，前端显示「待判定」而不是「无异常」。
        try:
            num = int(self.task.split("-")[-1])
            o = outcome_row(num)
            g = gate_row(num)
        except (ValueError, KeyError):
            o, g = {}, {}
        outcome = o.get("outcome_verdict") or None
        gate = g.get("gate_eligibility") or None

        return CaseSummary(
            caseId=self.task,
            runDir=self.bundle.run_dir,
            kind=getattr(self.bundle, "kind", "run"),
            auditApplicable=self.derived.applicable,
            auditNote=self.derived.not_applicable_reason,
            nEvents=len(self.bundle.events),
            nDeltas=len(self.bundle.deltas),
            nRecords=len(self.bundle.records),
            nNodes=len(self.bundle.nodes),
            tierCounts=self.derived.tier_counts,
            nAnomalies=len(self.derived.anomalies),
            anomalyTypes=atypes,
            outcome=outcome,
            outcomeLabel=i18n.label("outcome", outcome),
            gate=gate,
            gateLabel=i18n.label("partition", gate),
            integrityOk=None if not self.derived.applicable else bool(integ.get("ok")),
            replayOk=None if not self.derived.applicable else bool(rep.get("all_ok")),
            prediction=o.get("prediction") or None,
        )


def load(case_id: str | int) -> Case:
    bundle = get_source().load(case_id)
    return Case(bundle=bundle, derived=audit_svc.derive(bundle))


def available() -> List[dict]:
    return get_source().available()
