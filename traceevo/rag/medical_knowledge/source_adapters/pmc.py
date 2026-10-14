"""NCBI PMC Open Access adapter using BioC JSON plus ESummary metadata."""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
import threading
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from traceevo.rag.common.schemas import DocumentParagraph, DocumentSection, MedicalDocument
from traceevo.rag.medical_knowledge.licenses import LicensePolicy
from traceevo.rag.medical_knowledge.parser import parse_bioc_document, sections_from_bioc_payload
from traceevo.rag.medical_knowledge.storage import store_raw


BIOC_URL = "https://www.ncbi.nlm.nih.gov/research/bionlp/RESTful/pmcoa.cgi/BioC_json/{pmcid}/unicode"
ESUMMARY_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
_RATE_LIMIT_LOCK = threading.Lock()
_LAST_REQUEST_AT = 0.0


class PMCAdapterError(RuntimeError):
    pass


@dataclass(frozen=True)
class PMCSource:
    document: MedicalDocument
    paragraphs: list[DocumentParagraph]
    sections: list[DocumentSection]


class PMCOpenAccessAdapter:
    """Fetch one PMC OA article; non-OA, missing-license, and disallowed items fail closed."""

    def __init__(
        self,
        data_dir: str,
        *,
        license_policy: LicensePolicy | None = None,
        timeout_seconds: float = 30,
        email: str | None = None,
        user_agent: str = "TraceEvo-RAG/0.1 (local medical-knowledge ingestion)",
    ) -> None:
        self.data_dir = data_dir
        self.license_policy = license_policy or LicensePolicy()
        self.timeout_seconds = timeout_seconds
        self.email = email
        self.user_agent = user_agent

    def ingest(self, pmcid: str, specialty: list[str] | None = None) -> PMCSource:
        normalized = pmcid.upper().strip()
        if not re.fullmatch(r"PMC[0-9]+", normalized):
            raise ValueError("PMCID must have the form PMC123456")
        bio_c_bytes = self._get(BIOC_URL.format(pmcid=normalized))
        try:
            # This rejects BioC's HTML error documents and articles outside OA.
            raw_bioc = json.loads(bio_c_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PMCAdapterError(f"NCBI BioC returned a non-JSON response for {normalized}") from exc

        # Parse once with a temporary raw pointer to obtain and validate license
        # metadata before persisting any source bytes locally.
        metadata = self._esummary(normalized)
        source_uri = f"https://pmc.ncbi.nlm.nih.gov/articles/{normalized}/"
        document, paragraphs = parse_bioc_document(
            raw_bioc,
            pmcid=normalized,
            source_uri=source_uri,
            raw_path="pending-license-validation",
            metadata=metadata,
            specialty=specialty,
        )
        sections = sections_from_bioc_payload(raw_bioc, document_id=document.document_id)
        license_info = self.license_policy.require_ingestible(document.license, document.license_uri)
        raw_path = store_raw(self.data_dir, "pmc", bio_c_bytes, ".bioc.json")
        document = document.model_copy(
            update={"raw_path": raw_path, "license_id": license_info.identifier, "license_uri": license_info.uri}
        )
        return PMCSource(document=document, paragraphs=paragraphs, sections=sections)

    def _get(self, url: str) -> bytes:
        request = Request(url, headers={"User-Agent": self.user_agent, "Accept": "application/json"})
        last_error: Exception | None = None
        for attempt in range(4):
            self._respect_ncbi_rate_limit()
            try:
                with urlopen(request, timeout=self.timeout_seconds) as response:
                    return response.read()
            except HTTPError as exc:
                last_error = exc
                if exc.code not in {429, 500, 502, 503, 504} or attempt == 3:
                    break
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                try:
                    delay = min(30.0, max(0.5, float(retry_after))) if retry_after else 0.75 * (2 ** attempt)
                except ValueError:
                    delay = 0.75 * (2 ** attempt)
                time.sleep(delay)
            except (URLError, TimeoutError) as exc:
                last_error = exc
                if attempt == 3:
                    break
                time.sleep(0.5 * (2 ** attempt))
        raise PMCAdapterError(f"NCBI request failed after bounded retries: {last_error}") from last_error

    @staticmethod
    def _respect_ncbi_rate_limit() -> None:
        global _LAST_REQUEST_AT
        with _RATE_LIMIT_LOCK:
            now = time.monotonic()
            wait = 0.36 - (now - _LAST_REQUEST_AT)
            if wait > 0:
                time.sleep(wait)
            _LAST_REQUEST_AT = time.monotonic()

    def _esummary(self, pmcid: str) -> dict[str, Any]:
        params: dict[str, str] = {"db": "pmc", "id": pmcid[3:], "retmode": "json"}
        if self.email:
            params.update({"email": self.email, "tool": "traceevo_rag"})
        response = self._get(f"{ESUMMARY_URL}?{urlencode(params)}")
        try:
            payload = json.loads(response.decode("utf-8"))
            result = payload["result"]
            uid = result["uids"][0]
            record = result[uid]
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            raise PMCAdapterError(f"NCBI ESummary returned no metadata for {pmcid}") from exc
        ids = {item.get("idtype"): item.get("value") for item in record.get("articleids", []) if isinstance(item, dict)}
        returned_pmcid = str(ids.get("pmcid", "")).upper()
        if returned_pmcid and returned_pmcid != pmcid:
            raise PMCAdapterError(f"NCBI returned mismatched PMCID {returned_pmcid} for {pmcid}")
        return {
            "title": record.get("title"),
            "authors": [
                str(author.get("name", "")).strip()
                for author in record.get("authors", [])
                if isinstance(author, dict) and str(author.get("name", "")).strip()
            ],
            "publication_date": record.get("pubdate"),
            "doi": ids.get("doi"),
            "pmid": ids.get("pmid"),
        }
