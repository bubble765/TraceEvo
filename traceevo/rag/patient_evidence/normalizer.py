"""Normalization and alias matching without LLM or fuzzy retrieval."""
from __future__ import annotations

import re
import unicodedata
from typing import Iterable

from .aliases import EXAM_ALIASES, MODALITY_ALIASES, MODALITY_BY_EXAM


def _normalize(value: str) -> str:
    value = unicodedata.normalize("NFKC", str(value or "")).casefold().strip()
    value = re.sub(r"[^\w\u3400-\u9fff]+", " ", value, flags=re.UNICODE)
    return " ".join(value.split())


_EXAM_EXACT = {
    _normalize(alias): canonical
    for canonical, aliases in EXAM_ALIASES.items()
    for alias in aliases
}
_MODALITY_EXACT = {
    _normalize(alias): canonical
    for canonical, aliases in MODALITY_ALIASES.items()
    for alias in aliases
}


def _alias_pattern(alias: str) -> re.Pattern[str]:
    parts = [_normalize(part) for part in alias.split()]
    escaped = r"\s+".join(re.escape(part) for part in parts if part)
    # ASCII boundaries avoid matching e.g. `ct` inside an unrelated word;
    # Chinese aliases remain substring-matchable because they have no spaces.
    return re.compile(rf"(?<![a-z0-9]){escaped}(?![a-z0-9])", re.IGNORECASE)


_EXAM_PATTERNS = sorted(
    (
        (canonical, alias, _alias_pattern(alias))
        for canonical, aliases in EXAM_ALIASES.items()
        for alias in aliases
    ),
    key=lambda item: (-len(item[1]), item[0]),
)


def extract_exam_types(text: str) -> list[str]:
    """Return unique canonical exam types in first-mention order.

    Overlapping aliases prefer the longest phrase, so "CT angiography" is CTA,
    not both CTA and CT. This function labels mentions only; it does not infer
    whether an examination was actually performed or what its result was.
    """
    value = _normalize(str(text or ""))
    matches: list[tuple[int, int, str]] = []
    occupied: list[tuple[int, int]] = []
    for canonical, _alias, pattern in _EXAM_PATTERNS:
        for match in pattern.finditer(value):
            start, end = match.span()
            if any(start < used_end and end > used_start
                   for used_start, used_end in occupied):
                continue
            matches.append((start, end, canonical))
            occupied.append((start, end))
    matches.sort(key=lambda item: (item[0], item[1]))
    out: list[str] = []
    for _start, _end, canonical in matches:
        if canonical not in out:
            out.append(canonical)
    return out


def normalize_exam_type(value: str | None) -> str | None:
    """Normalize an exact alias; accept a phrase only when it names one exam."""
    normalized = _normalize(value or "")
    if not normalized:
        return None
    if normalized in EXAM_ALIASES:
        return normalized
    if normalized in _EXAM_EXACT:
        return _EXAM_EXACT[normalized]
    found = extract_exam_types(value or "")
    return found[0] if len(found) == 1 else None


def normalize_modality(value: str | None) -> str | None:
    normalized = _normalize(value or "")
    if not normalized:
        return None
    if normalized in _MODALITY_EXACT:
        return _MODALITY_EXACT[normalized]
    normalized_text = _normalize(value)
    hits = [canonical for alias, canonical in _MODALITY_EXACT.items()
            if re.search(_alias_pattern(alias), normalized_text)]
    unique = list(dict.fromkeys(hits))
    exam_modalities = list(dict.fromkeys(
        modality_for_exam_type(kind) for kind in extract_exam_types(value)))
    exam_modalities = [m for m in exam_modalities if m != "unknown"]
    combined = list(dict.fromkeys([*unique, *exam_modalities]))
    return combined[0] if len(combined) == 1 else None


def modality_for_exam_type(exam_type: str | None) -> str:
    canonical = normalize_exam_type(exam_type)
    return MODALITY_BY_EXAM.get(canonical or "", "unknown")


def infer_modality(exam_types: Iterable[str]) -> str:
    modalities = list(dict.fromkeys(modality_for_exam_type(x) for x in exam_types))
    modalities = [m for m in modalities if m != "unknown"]
    if not modalities:
        return "unknown"
    return modalities[0] if len(modalities) == 1 else "mixed"


def canonical_exam_filter(value: str) -> str:
    """Normalize a requested filter; unknown names stay exact, never broaden."""
    canonical = normalize_exam_type(value)
    if canonical:
        return canonical
    return re.sub(r"\s+", "_", _normalize(value))
