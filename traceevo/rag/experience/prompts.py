"""Embedding and reranking instructions for Experience task similarity."""

EXPERIENCE_QUERY_INSTRUCTION = (
    "Given only the current task's initial presenting symptoms and initial "
    "examination, retrieve prior tasks with similar initial presentation. Do "
    "not use later test results, hypotheses, diagnoses, or outcomes to match."
)

EXPERIENCE_RERANK_INSTRUCTION = (
    "Compare the current task's initial symptoms and examination with a prior "
    "task's initial presentation to judge whether its collaboration strategy is "
    "applicable. Do not use later findings, diagnoses, or outcomes to rank the "
    "prior task higher."
)
