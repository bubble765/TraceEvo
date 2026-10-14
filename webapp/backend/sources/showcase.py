"""手写展示轨迹的数据源。

**这是本系统里唯一一处「数据不是跑出来的」。** 参考项目
`医疗溯因智能体/gos_repo` 的 showcase（`viz/stories.py` 的 `ankle`、
`demo/ankle_showcase.trace.json`）也是手写的 —— 想要一个「两级假设 +
一次深挖 + 一次回溯」的干净叙事，管线跑不出来：参考项目**自己真跑**
同一个场景得到的是 6 节点 / 1 假设（`outputs/conf_verify_v2/traces/case_0.json`），
我们真跑是 12 节点 / 5 假设，都没有那个两级结构。

所以这份数据是**编排**出来的。随之而来的边界必须焊死在代码里：

  * 手写的只能是**剧情** —— 因果图、置信度、推理步骤、专家与工具调用。
  * 手写的不能是**判定** —— `kind = "showcase"` 让 `services/audit.py`
    **不去跑**审计。跑了会从这份数据里算出一组 P0–P3，而它们看起来与真实
    运行的审计结论一模一样。

数据文件在 `webapp/showcase/*.json`，生成脚本见同目录的
`build_ankle_showcase.py`。文件里刻意**没有**的内容（integrity / replay /
ledger / 来源类 / 匹配度）一律保持缺席 —— 缺席在界面上显示成「不适用」，
而补一个默认值会让它看起来像「判定为无异常」。
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List

HERE = os.path.dirname(os.path.abspath(__file__))
SHOWCASE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(HERE)), "showcase")


def _paths() -> List[str]:
    if not os.path.isdir(SHOWCASE_DIR):
        return []
    return sorted(os.path.join(SHOWCASE_DIR, f)
                  for f in os.listdir(SHOWCASE_DIR) if f.endswith(".json"))


def catalogue() -> List[Dict[str, Any]]:
    """全部展示轨迹的登记信息。读不动的文件跳过而不是让服务起不来。"""
    out = []
    for p in _paths():
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            continue
        if d.get("case_id"):
            out.append({"caseId": d["case_id"],
                        "label": d.get("label") or d["case_id"],
                        "note": d.get("note") or "",
                        "path": p})
    return out


def case_refs() -> List[Dict[str, Any]]:
    """`/api/cases` 的一行（`CaseRef` 的形状），供 `_Dispatch.available` 拼表。

    **这个函数存在的理由是「只能有一处往病例列表里加展示轨迹」。** 曾经
    `config.all_cases` 与 `_Dispatch.available` 各加了一次 —— 结果
    `case-ankle` 在响应里出现两遍，其中一遍是 `catalogue()` 的原始形状，
    没有 `kind` 字段。那一条在前端会被读成「不是 showcase」，与旁边那条
    自相矛盾；而且两条同 id 的记录进任何按 id 索引的表都是后写的赢，
    赢的是没有 `kind` 的那条。现在拼表只在 `_Dispatch.available` 一处。

    `caseNumber` 是 None 而不是 0：展示轨迹没有病例号，而 0 是个合法号
    （case-0 真实存在），拿它当「没有」会让两者在界面上撞在一起。
    """
    from ..config import is_demo
    return [{"caseId": e["caseId"], "caseNumber": None,
             "runId": "showcase", "runLabel": e["label"],
             "kind": "showcase", "isDemo": is_demo(e["caseId"])}
            for e in catalogue()]


def find(case_id: str) -> Dict[str, Any] | None:
    for entry in catalogue():
        if entry["caseId"] == case_id:
            return entry
    return None


class ShowcaseBundle:
    """与 `ArtifactBundle` 同协议。字段缺席的地方**保持缺席**。"""

    kind = "showcase"

    def __init__(self, entry: Dict[str, Any]) -> None:
        with open(entry["path"], encoding="utf-8") as f:
            d = json.load(f)
        self.case_id = d["case_id"]
        self.task = d["case_id"]
        self.run_dir = entry["path"]
        self.showcase_label = d.get("label") or d["case_id"]
        self.showcase_note = d.get("note") or ""

        self.events = d.get("events") or []
        self.deltas = d.get("deltas") or []
        self.nodes = d.get("bindings") or []
        self.records = d.get("records") or []
        self.provenance_graph = d.get("provenance_graph") or {}
        self.summary = d.get("summary") or {}
        self.grounding = d.get("grounding") or {}
        # 四类**审计产物**：展示轨迹没有，也不该伪造。
        self.ledger: List[Dict[str, Any]] = []
        self.injections: List[Dict[str, Any]] = []
        self.integrity: Dict[str, Any] = {}
        self.replay: Dict[str, Any] = {}
        self.loaded = {name: False for name in
                       ("events", "deltas", "bindings", "provenance_graph",
                        "invocations", "injections", "integrity", "replay",
                        "summary")}
        self.loaded.update({"events": bool(self.events),
                            "deltas": bool(self.deltas),
                            "bindings": bool(self.nodes),
                            "summary": bool(self.summary)})


class ShowcaseSource:
    def load(self, case_id: str) -> ShowcaseBundle:
        entry = find(case_id)
        if entry is None:
            raise KeyError(f"{case_id} 不在展示轨迹里")
        return ShowcaseBundle(entry)

    def available(self) -> List[Dict[str, Any]]:
        return catalogue()
