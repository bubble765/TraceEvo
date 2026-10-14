"""从落盘的 artifact 读取病例。演示模式与历史浏览都用它。

**只读。** 本模块不写任何文件、不重跑任何模型调用、不重算任何判定 ——
封存产物是不可变事实，界面展示的就是它们。

一个刻意的选择：文件缺失时返回空结构而不是抛异常。真实分析模式下，
病例正在运行时 `_bindings.json` 可能还没落盘，前端应当看到一张空图
而不是一个 500。哪些字段「还没到」由 `loaded` 集合说明，前端据此显示
「进行中」而不是「无数据」。
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from .. import config

SUFFIXES = {
    "events": "_events.jsonl",
    "nodes": "_bindings.json",
    "deltas": "_deltas.jsonl",
    "ledger": "_invocations.jsonl",
    "injections": "_injections.jsonl",
    "integrity": "_integrity.json",
    "replay": "_replay.json",
    "summary": "_summary.json",
    "provenance_graph": "_provenance_graph.json",
}


def _read_jsonl(path: str) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        return []
    out: List[Dict[str, Any]] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    # 一个正在被写入的文件可能读到半行。跳过它，下轮再读。
                    continue
    return out


def _read_json(path: str, default: Any = None) -> Any:
    if not os.path.exists(path):
        return {} if default is None else default
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        return {} if default is None else default


class Bundle:
    """落盘产物的内存视图。字段名与文件一一对应。"""

    # 真实运行的产物。手写展示轨迹（`sources/showcase.py`）用 "showcase" ——
    # 这个标记决定审计层跑不跑，**不能靠猜**：从手写数据里算出 P0–P3 会得到
    # 一组看起来像审计结论的东西，而它没有资格下任何结论。
    kind = "run"

    def __init__(self, case_id: str, run_dir: str) -> None:
        self.case_id = case_id
        self.run_dir = run_dir
        self.task = config.case_task(case_id)
        t = self.task

        self.events = _read_jsonl(os.path.join(run_dir, f"{t}_events.jsonl"))
        self.deltas = _read_jsonl(os.path.join(run_dir, f"{t}_deltas.jsonl"))
        self.ledger = _read_jsonl(os.path.join(run_dir, f"{t}_invocations.jsonl"))
        self.injections = _read_jsonl(os.path.join(
            run_dir, f"{t}_injections.jsonl"))
        self.integrity = _read_json(os.path.join(run_dir, f"{t}_integrity.json"))
        self.replay = _read_json(os.path.join(run_dir, f"{t}_replay.json"))
        self.summary = _read_json(os.path.join(run_dir, f"{t}_summary.json"))
        self.provenance_graph = _read_json(os.path.join(
            run_dir, f"{t}_provenance_graph.json"))

        bindings = _read_json(os.path.join(run_dir, f"{t}_bindings.json"))
        self.nodes: List[Dict[str, Any]] = bindings.get("bindings") or []
        self.records: List[Dict[str, Any]] = bindings.get("records") or []
        self.grounding: Dict[str, Any] = bindings.get("grounding_summary") or {}

        # 哪些产物已经落盘。前端用它区分「还没生成」与「生成后为空」。
        self.loaded = {
            name: os.path.exists(os.path.join(run_dir, f"{t}{suf}"))
            for name, suf in SUFFIXES.items()
        }

    # ------------------------------------------------------------- 便捷访问
    def event(self, event_id: str) -> Optional[Dict[str, Any]]:
        for e in self.events:
            if e.get("event_id") == event_id:
                return e
        return None

    def node(self, node_id: str) -> Optional[Dict[str, Any]]:
        bare = node_id.split(":", 1)[-1] if node_id.startswith("gos:") \
            else node_id
        for n in self.nodes:
            if n.get("node_id") == bare:
                return n
        return None

    def record(self, record_id: str) -> Optional[Dict[str, Any]]:
        for r in self.records:
            if r.get("record_id") == record_id:
                return r
        return None

    @property
    def is_complete(self) -> bool:
        """产物是否已全部落盘。真实分析模式下用来显示「进行中」。

        判据取 `summary` 与 `nodes` 两项：前者由收尾时写出，后者是图的
        载体。单独任一项存在都不足以说明病例跑完了 —— 一个跑到一半的
        病例会有 events 而没有 summary。
        """
        return bool(self.loaded.get("summary") and self.loaded.get("nodes"))


# ------------------------------------------------------------------ 数据源
class ArtifactSource:
    """按病例号解析运行目录并读取。"""

    name = "artifact"

    def load(self, case_id: str) -> Bundle:
        run_dir = config.run_dir_for(case_id)
        return Bundle(case_id, run_dir)

    def available(self) -> List[Dict[str, Any]]:
        return config.all_cases()


# ------------------------------------------------------------------ 判题与门控
# 这两类产物在阶段交付目录里，不在运行目录里，所以单独读一次并缓存。
_CACHE: Dict[str, Dict[str, Dict[str, str]]] = {}


def _csv_index(path: str, key: str = "case") -> Dict[str, Dict[str, str]]:
    import csv
    if path in _CACHE:
        return _CACHE[path]
    out: Dict[str, Dict[str, str]] = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                out[str(row.get(key, ""))] = row
    _CACHE[path] = out
    return out


def outcome_row(case_number: int) -> Dict[str, str]:
    return _csv_index(os.path.join(
        config.S5B_OUT, "outcome_results.csv")).get(str(case_number), {})


def gate_row(case_number: int) -> Dict[str, str]:
    return _csv_index(os.path.join(
        config.S5B_OUT, "natural_experience_summary.csv")).get(
        str(case_number), {})


def candidate_row(case_number: int) -> Dict[str, Any]:
    """`gate_candidates.jsonl` 里该病例的完整候选（含渲染所需的 payload）。"""
    task = f"case-{case_number}"
    path = os.path.join(config.S5B_OUT, "gate_candidates.jsonl")
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("source_trace_id") == task or obj.get("task_id") == task:
                return obj
    return {}
