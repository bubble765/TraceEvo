"""Source-aware Medical Knowledge ingestion (kept separate from patient facts)."""

from .licenses import IngestionLicenseError, LicensePolicy, normalize_license
from .parser import MedicalIngestionBundle, paragraphs_from_sections, parse_bioc_document, parse_bioc_json
from .splitter import (
    DocumentChunkConfig,
    DocumentChunkSplitter,
    MedicalEntityLexicon,
    ParentChildSplitter,
    RegexTokenCounter,
    TokenCounter,
)

__all__ = [
    "IngestionLicenseError",
    "LicensePolicy",
    "MedicalIngestionBundle",
    "DocumentChunkConfig",
    "DocumentChunkSplitter",
    "MedicalEntityLexicon",
    "ParentChildSplitter",
    "RegexTokenCounter",
    "TokenCounter",
    "normalize_license",
    "paragraphs_from_sections",
    "parse_bioc_document",
    "parse_bioc_json",
]
