"""病例列表与概览。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..services import case as case_svc

router = APIRouter()


@router.get("/cases")
def list_cases():
    """系统当前可浏览的全部病例。演示与历史任务在此并列，标注 kind。"""
    return {"cases": case_svc.available()}


@router.get("/cases/{case_id}/summary")
def summary(case_id: str):
    try:
        return case_svc.load(case_id).summary()
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
