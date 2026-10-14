"""Conservative reuse policy for source documents that will be chunked/indexed."""
from __future__ import annotations

from dataclasses import dataclass
import re


class IngestionLicenseError(ValueError):
    """Raised when a source's reuse terms are absent, unclear, or disallowed."""


@dataclass(frozen=True)
class LicenseInfo:
    identifier: str
    uri: str | None
    permits_derivatives: bool
    commercial_use: bool


@dataclass(frozen=True)
class LicensePolicy:
    """Allow only explicit CC/CC0 terms; NC licenses require an opt-in.

    This is an ingestion guard, not legal advice. It intentionally rejects
    NoDerivatives, custom, and missing terms because this pipeline creates
    adapted child chunks and embeddings.
    """

    allow_noncommercial: bool = False

    def require_ingestible(self, license_text: str | None, license_uri: str | None = None) -> LicenseInfo:
        info = normalize_license(license_text, license_uri)
        if info is None:
            raise IngestionLicenseError("missing or unrecognized reuse license; refusing to ingest")
        if not info.permits_derivatives:
            raise IngestionLicenseError(f"{info.identifier} does not permit derivative chunking")
        if not info.commercial_use and not self.allow_noncommercial:
            raise IngestionLicenseError(
                f"{info.identifier} is noncommercial; pass the explicit noncommercial-use opt-in to ingest"
            )
        return info


def normalize_license(text: str | None, uri: str | None = None) -> LicenseInfo | None:
    """Recognize a small allowlist of standard Creative Commons licenses."""
    combined = f"{text or ''} {uri or ''}".strip()
    if not combined:
        return None
    folded = combined.casefold().replace("_", "-")
    urls = re.findall(r"https?://[^\s)\]}>,;]+", combined, flags=re.IGNORECASE)
    found_uri = uri or next((u.rstrip(".)") for u in urls if "creativecommons.org" in u.casefold()), None)

    # Prefer versioned machine identifiers (including WHO's IGO suffix) and
    # also accept the abbreviated license fields returned by PMC BioC.
    code_match = re.search(r"\bcc\s*(by(?:\s*[- ]\s*(?:nc|sa|nd)){0,2})(?:\s+([0-9]+(?:\.[0-9]+)*)(?:\s+igo)?)?", folded)
    path_match = re.search(r"creativecommons\.org/licenses/([a-z-]+)/([0-9.]+)", folded)
    code = code_match.group(1) if code_match else (path_match.group(1).replace("-", " ") if path_match else None)
    version = (code_match.group(2) or (path_match.group(2) if path_match else None)) if code_match else (path_match.group(2) if path_match else None)
    if not code and "creative commons" in folded and "attribution" in folded:
        # BioC sometimes carries the human-readable legal name but omits the
        # linked license URI. Reconstruct only standard, explicit CC variants.
        code = "by"
        if re.search(r"non[\s-]?commercial", folded):
            code += "-nc"
        if re.search(r"no[\s-]?derivatives", folded):
            code += "-nd"
        if re.search(r"share[\s-]?alike", folded):
            code += "-sa"
        version_match = re.search(r"\b([0-9]+(?:\.[0-9]+)+)\b", folded)
        version = version_match.group(1) if version_match else None
    if not code and re.search(r"\bcc0\b|public domain|creativecommons\.org/publicdomain/zero", folded):
        return LicenseInfo("CC0-1.0", found_uri or "https://creativecommons.org/publicdomain/zero/1.0/", True, True)
    if not code:
        return None
    tokens = set(re.findall(r"by|nc|sa|nd", code.replace(" ", "-")))
    # The canonical CC short name always includes BY; reject odd lookalikes.
    if "by" not in tokens:
        return None
    if "nd" in tokens:
        return LicenseInfo("CC BY-ND" + (f" {version}" if version else ""), found_uri, False, "nc" not in tokens)

    suffix = "".join(f"-{part.upper()}" for part in ("nc", "sa") if part in tokens)
    identifier = "CC BY" + suffix + (f" {version}" if version else "")
    if "igo" in folded and "3.0" in folded:
        identifier += " IGO"
    return LicenseInfo(identifier, found_uri, True, "nc" not in tokens)
