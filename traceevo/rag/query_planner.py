"""Mandatory, store-scoped query reformulation through structured output."""
from __future__ import annotations

import json
import re
from typing import Any, Protocol

from pydantic import BaseModel

from traceevo.rag.common.schemas import QueryPlan, RagStore


class StructuredOutputProvider(Protocol):
    """Adapter seam for an application-configured LLM structured-output call."""

    def generate_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        output_model: type[BaseModel],
    ) -> Any: ...


class OpenAICompatibleStructuredOutputProvider:
    """Thin adapter for an injected OpenAI-compatible chat-completions client.

    The RAG package does not create a client or read credentials itself; the
    host application supplies its configured client and model name.
    """

    def __init__(self, client: Any, *, model: str) -> None:
        if not model.strip():
            raise ValueError("model name cannot be empty")
        self.client = client
        self.model = model

    def generate_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        output_model: type[BaseModel],
    ) -> Any:
        schema = self._strict_schema(output_model.model_json_schema())
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": re.sub(r"[^A-Za-z0-9_-]", "_", output_model.__name__).lower(),
                    "strict": True,
                    "schema": schema,
                },
            },
        )
        message = response.choices[0].message
        if getattr(message, "refusal", None):
            raise QueryPlanningError(f"structured-output provider refused the query: {message.refusal}")
        parsed = getattr(message, "parsed", None)
        if parsed is not None:
            return parsed
        content = getattr(message, "content", None)
        if not isinstance(content, str) or not content.strip():
            raise QueryPlanningError("structured-output provider returned no JSON content")
        return content

    @classmethod
    def _strict_schema(cls, schema: dict[str, Any]) -> dict[str, Any]:
        """Adapt Pydantic JSON Schema to strict Structured Outputs requirements."""
        clean = {key: value for key, value in schema.items() if key not in {"title", "default"}}
        definitions = clean.get("$defs")
        if isinstance(definitions, dict):
            clean["$defs"] = {key: cls._strict_schema(value) for key, value in definitions.items()}
        properties = clean.get("properties")
        if isinstance(properties, dict):
            clean["properties"] = {key: cls._strict_schema(value) for key, value in properties.items()}
            clean["required"] = list(properties)
            clean["additionalProperties"] = False
        if isinstance(clean.get("items"), dict):
            clean["items"] = cls._strict_schema(clean["items"])
        for union_key in ("anyOf", "oneOf", "allOf"):
            if isinstance(clean.get(union_key), list):
                clean[union_key] = [cls._strict_schema(item) for item in clean[union_key]]
        return clean


SYSTEM_PROMPT = """You are TraceEvo's retrieval query planner. Do not answer the medical question.
Transform each input into retrieval-ready query text and return only a QueryPlan.

Rules:
1. Reformulate every input, including simple lookups; do not copy the original query verbatim.
2. Use mode=single with exactly one self-contained query for one atomic information need.
3. Use mode=decompose with 2-4 non-overlapping subqueries only when needs are independent.
4. Query text must be retrieval-oriented, preserve the user's intent, and not add unsupported facts.
5. For differential diagnosis, retrieve discriminative evidence that can support or refute alternatives.
6. Never turn an unverified hypothesis into a patient fact or insert a diagnosis not supplied by the user.
7. For patient_evidence, task_id is a caller-owned retrieval scope and is not part of the model output.
   Extract exam_type or modality only when explicitly implied; never invent examination results.
8. For medical_knowledge, ask about general medical knowledge, not facts about the current patient.
9. For experience, describe task characteristics and process needs, not a desired diagnosis.
10. Set store to the requested store and return schema-compatible JSON only."""


class QueryPlanningError(ValueError):
    """Raised when structured output is missing, invalid, or unsafe to retrieve."""


class QueryPlanner:
    """Call one injected structured-output provider for every incoming query."""

    def __init__(self, provider: StructuredOutputProvider) -> None:
        self.provider = provider

    def plan(
        self,
        query: str,
        *,
        store: RagStore,
        task_id: str | None = None,
    ) -> QueryPlan:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query cannot be empty")
        try:
            store = RagStore(store)
        except ValueError as exc:
            raise ValueError(f"unsupported RAG store: {store!r}") from exc
        if store is RagStore.PATIENT_EVIDENCE and not str(task_id or "").strip():
            raise ValueError("task_id is mandatory for Patient Evidence query planning")
        if store is not RagStore.PATIENT_EVIDENCE and task_id is not None:
            raise ValueError("task_id is only accepted for Patient Evidence query planning")

        request = {
            "requested_store": store.value,
            "user_query": query,
        }
        try:
            raw = self.provider.generate_structured(
                system_prompt=SYSTEM_PROMPT,
                user_prompt=json.dumps(request, ensure_ascii=False),
                output_model=QueryPlan,
            )
            plan = self._coerce_plan(raw)
        except QueryPlanningError:
            raise
        except Exception as exc:
            raise QueryPlanningError(f"structured query planning failed: {exc}") from exc

        if plan.store is not store:
            raise QueryPlanningError(
                f"planner returned store={plan.store.value!r} for requested store={store.value!r}"
            )
        original = self._normalize_for_comparison(query)
        if any(self._normalize_for_comparison(item.query_text) == original for item in plan.queries):
            raise QueryPlanningError("planner must rewrite the original query rather than copy it verbatim")
        if any(not item.query_text.strip() for item in plan.queries):
            raise QueryPlanningError("planner returned an empty subquery")
        return plan

    @staticmethod
    def _coerce_plan(raw: Any) -> QueryPlan:
        try:
            if isinstance(raw, QueryPlan):
                return raw
            if isinstance(raw, str):
                return QueryPlan.model_validate_json(raw)
            if isinstance(raw, BaseModel):
                raw = raw.model_dump(mode="python")
            return QueryPlan.model_validate(raw)
        except Exception as exc:
            raise QueryPlanningError(f"provider output does not match QueryPlan: {exc}") from exc

    @staticmethod
    def _normalize_for_comparison(value: str) -> str:
        return re.sub(r"[^\w]+", "", value, flags=re.UNICODE).casefold()
