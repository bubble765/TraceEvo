"""Read-only catalog for Patient Evidence, Medical Knowledge, and Experience."""
from __future__ import annotations

from fastapi import APIRouter

from ..schemas import KnowledgeBasesView
from ..services.knowledge_bases import build_knowledge_bases_view

router = APIRouter()


@router.get("/knowledge-bases", response_model=KnowledgeBasesView)
def knowledge_bases() -> KnowledgeBasesView:
    return build_knowledge_bases_view()
