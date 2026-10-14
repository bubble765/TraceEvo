"""经验中心：结果验证、门控与经验卡。

注意这里**不返回完整轨迹**。轨迹走 `/raw/{artifact}`，经验卡只含抽象内容。
两者的分流是 §5.2 的隐私边界在接口层面的落点 —— 不是靠前端不显示，
而是那个接口根本不提供。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..services import case as case_svc

router = APIRouter()


@router.get("/cases/{case_id}/experience")
def experience(case_id: str):
    try:
        return case_svc.load(case_id).experience()
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
