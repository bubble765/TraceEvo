"""执行档案：直出封存产物原文。

**白名单是硬编码的，不是遍历目录得到的。** 遍历目录会把任何新增的落盘
文件自动暴露出去 —— 包括将来可能出现的、含患者原文或参考答案的中间产物。
白名单挡住的不是路径穿越（那由 `os.path.join` + 固定名单解决），而是
「未来某天有人加了一个新产物，它悄悄出现在对外接口上」这件事。

执行档案页用它展示**为审计永久保存的完整轨迹**，与经验卡（只含抽象内容）
形成对照。这个对照是产品要讲的东西之一，所以接口必须能取到原文。
"""
from __future__ import annotations

import os
from typing import Any, Dict

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse

from .. import config
from ..sources.artifact import SUFFIXES

router = APIRouter()

# 允许直出的产物。键是 URL 里的名字，值是文件后缀。
WHITELIST: Dict[str, str] = {
    "events": SUFFIXES["events"],
    "bindings": SUFFIXES["nodes"],
    "deltas": SUFFIXES["deltas"],
    "invocations": SUFFIXES["ledger"],
    "injections": SUFFIXES["injections"],
    "integrity": SUFFIXES["integrity"],
    "replay": SUFFIXES["replay"],
    "summary": SUFFIXES["summary"],
    "provenance-graph": SUFFIXES["provenance_graph"],
}

DESCRIPTIONS: Dict[str, str] = {
    "events": "事件日志（含哈希链）",
    "bindings": "节点绑定与检索记录",
    "deltas": "推理图增量序列",
    "invocations": "模型调用账本",
    "injections": "异常注入记录",
    "integrity": "哈希链完整性校验结果",
    "replay": "增量重放校验结果",
    "summary": "运行汇总",
    "provenance-graph": "事件溯源图（依赖边与因果节点引用）",
}


def _showcase(case_id: str):
    """展示轨迹 -> 登记信息；否则 None。"""
    from ..sources import showcase
    return showcase.find(config.case_task(case_id))


def _na(case_id: str, why: str) -> Dict[str, Any]:
    """「没有落盘产物」与「展示轨迹根本没有产物」是两件事。

    前者是「还没生成」，后者是「不存在」。都用 `available: False` 表达的话，
    执行档案页会把一份手写轨迹显示成「尚未落盘」，读起来像在等它落盘。
    """
    return {"caseId": config.case_task(case_id), "applicable": False,
            "note": why, "artifacts": []}


@router.get("/cases/{case_id}/raw")
def list_raw(case_id: str):
    """本病例有哪些产物、是否已落盘。"""
    s = _showcase(case_id)
    if s:
        return _na(case_id, "展示轨迹：没有落盘的原始产物，只有编排的展示数据")
    try:
        run_dir = config.run_dir_for(case_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    task = config.case_task(case_id)
    return {
        "caseId": task,
        "applicable": True,
        "note": "",
        "artifacts": [
            {"name": name, "description": DESCRIPTIONS[name],
             "available": os.path.exists(os.path.join(run_dir, f"{task}{suf}"))}
            for name, suf in WHITELIST.items()
        ],
    }


@router.get("/cases/{case_id}/raw/{artifact}", response_class=PlainTextResponse)
def raw(case_id: str, artifact: str) -> Any:
    if artifact not in WHITELIST:
        raise HTTPException(status_code=404, detail=f"未知产物：{artifact}")
    if _showcase(case_id):
        raise HTTPException(
            status_code=404,
            detail="展示轨迹没有落盘的原始产物 —— 它的数据是编排的，"
                   "不是某次运行写出来的")
    try:
        run_dir = config.run_dir_for(case_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))

    path = os.path.join(run_dir, f"{config.case_task(case_id)}{WHITELIST[artifact]}")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="该产物尚未落盘")
    with open(path, encoding="utf-8") as f:
        return PlainTextResponse(f.read(), media_type="application/json")
