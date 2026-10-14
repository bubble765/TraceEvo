"""数据源协议。

数据源只做一件事：给定病例号，交出它落盘的原始产物。所有加工都在
`services/` 里，所以「从封存 artifact 读」与「从正在运行的病例读」
只要交出同样的东西，上层就连同一条代码路径 —— 这是演示模式与真实
分析模式共用可视化协议的实现方式，不是一句约定。
"""
from __future__ import annotations

from typing import Any, Dict, List, Protocol, runtime_checkable


@runtime_checkable
class ArtifactBundle(Protocol):
    """一个病例的全部产物。字段名与落盘文件一一对应。"""

    case_id: str
    run_dir: str
    task: str

    events: List[Dict[str, Any]]        # {task}_events.jsonl
    nodes: List[Dict[str, Any]]         # bindings.json -> bindings
    records: List[Dict[str, Any]]       # bindings.json -> records
    deltas: List[Dict[str, Any]]        # {task}_deltas.jsonl
    ledger: List[Dict[str, Any]]        # {task}_invocations.jsonl
    injections: List[Dict[str, Any]]    # {task}_injections.jsonl
    integrity: Dict[str, Any]           # {task}_integrity.json
    replay: Dict[str, Any]              # {task}_replay.json
    summary: Dict[str, Any]             # {task}_summary.json
    provenance_graph: Dict[str, Any]    # {task}_provenance_graph.json
    grounding: Dict[str, Any]           # bindings.json -> grounding_summary


@runtime_checkable
class DataSource(Protocol):
    def load(self, case_id: str) -> ArtifactBundle: ...
    def available(self) -> List[Dict[str, Any]]: ...
