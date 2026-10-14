"""审计面板与模型调用账本。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..services import case as case_svc

router = APIRouter()


def _load(case_id: str):
    try:
        return case_svc.load(case_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/cases/{case_id}/audit")
def audit(case_id: str):
    return _load(case_id).audit()


@router.get("/cases/{case_id}/ledger")
def ledger(case_id: str):
    return _load(case_id).ledger()
