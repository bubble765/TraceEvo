"""双图、增量与双向联动索引。

`/reasoning-graph?step=N` 的 `step` 是**回放位置**（0 起，含该步）。
不传则给出最终图。两者走同一条重建代码路径（`reasoning_graph.build`），
所以「回放到最后一步」与「最终图」在结构上不可能不一致。
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from ..services import case as case_svc

router = APIRouter()


def _load(case_id: str):
    try:
        return case_svc.load(case_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/cases/{case_id}/reasoning-graph")
def reasoning_graph(case_id: str,
                    step: Optional[int] = Query(None, ge=0)):
    return _load(case_id).reasoning(upto=step)


@router.get("/cases/{case_id}/provenance-graph")
def provenance_graph(case_id: str,
                     step: Optional[int] = Query(None, ge=0)):
    """执行溯源图。**与推理图一样接受 `?step=N`。**

    两张图都按步取，是刻意的对称：左图在长出来而右图一动不动，会被读成
    「右边那张是背景」。这条曾经就是那样，而它恰好把本系统要展示的东西
    ——协作过程——跳过去了。
    """
    return _load(case_id).provenance(upto=step)


@router.get("/cases/{case_id}/deltas")
def deltas(case_id: str):
    return {"caseId": case_id, "deltas": _load(case_id).deltas()}


@router.get("/cases/{case_id}/binding")
def binding(case_id: str):
    return _load(case_id).binding()


@router.get("/cases/{case_id}/demo-script")
def demo_script(case_id: str):
    return _load(case_id).demo_script()
