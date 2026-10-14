"""Prepare a blinded, source-disjoint RareArena RDC pilot for experience tests.

The online run sees only the three GoS input fields written to
``dataset_blinded_build.csv``. Gold diagnoses are written to a separate vault
file for a later, post-seal verifier process. The retrieval test file contains
only pre-task case narratives; it contains no tests or labels.

RareArena's public RDC benchmark is licensed CC BY-NC-SA 4.0. Keep the raw
source and derived files in the ignored experiment-data directory and retain
the attribution manifest if results are shared.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import re
from collections import Counter
from pathlib import Path
from typing import Any

from traceevo.evolution.leakage_guard import ngrams


SOURCE_URL = (
    "https://github.com/zhao-zy15/RareArena/blob/master/"
    "benchmark_data/RDC_benchmark.jsonl"
)
LICENSE = "CC BY-NC-SA 4.0"
ATTRIBUTION = (
    "RareArena: a comprehensive benchmark dataset unveiling the potential "
    "of large language models in rare disease diagnosis; Chen et al.; "
    "The Lancet Digital Health (2026); DOI:10.1016/j.landig.2025.100953."
)

# Manual curation after inspecting only the sanitized, still-blinded text.
# These records are not suitable as symptom-matching queries because their
# retained narrative is dominated by treatment progression, established
# diagnoses, or diagnostic workup rather than a presenting clinical picture.
_MANUAL_RETRIEVAL_EXCLUSIONS = {
    "3220149-1": "orthodontic treatment course dominates the narrative",
    "7393846-1": "established retinal diagnosis and specialist follow-up dominate",
    "8872394-1": "established congenital diagnoses, treatment, and carrier status dominate",
    "3665137-1": "presenting text includes an active bone-scan workup",
    "3419076-1": "known dextrocardia referral without a symptom-led presentation",
    "11114410-2": "long post-injury treatment course dominates the narrative",
    "5812144-1": "post-myocardial-infarction inpatient stroke course dominates",
    "8520756-1": "established systemic diagnosis and antibody workup dominate",
    "10022842-1": "established endocrine labels and adrenal imaging dominate",
    "6159015-1": "known aneurysm diagnoses and embolization course dominate",
    "7667318-1": "prenatal diagnosis, termination, and autopsy outcomes dominate",
    "6286578-1": "ethics/consent preamble dominates the short symptom-led history",
    "6957238-1": "established pathology diagnosis and extensive later workup dominate",
    "5827144-1": "old traumatic injury, established sequelae, and repeated surgeries dominate",
    "5538080-1": "long-standing multisystem history dominates the new symptom presentation",
    "9210875-1": "epilepsy and brain imaging diagnosis are established in the opening history",
    "9329594-1": "established epilepsy, long-term medication, and drug-level result dominate",
    "8472909-1": "multiple established congenital findings and airway interventions dominate",
}

# The retrieval key must describe the pre-test presentation. RareArena's
# ``case_report`` field is not perfectly uniform: a minority of rows already
# mention imaging, laboratory, pathology, or molecular results there, in
# addition to the separate ``test_results`` field. Exclude those rows instead
# of silently letting post-test findings enter the experience query.
_EARLY_TEST_RE = re.compile(
    r"\b(?:laboratory|lab results?|blood tests?|blood chemistry results?|"
    r"routine blood samples?|blood samples? (?:taken|drawn|collected)|"
    r"blood and urine tests?|routine blood and urine tests?|"
    r"routine urine tests?|urine tests? (?:showed|were)|"
    r"initial blood chemistry|initial blood test results?|urinalysis|serology|"
    r"computed tomography|ct scan|mri|magnetic resonance imaging|"
    r"electroencephal(?:ogram|ography)|\beeg\b|"
    r"electrocardiogram|\becg\b|\bekg\b|ultrasound|sonograph(?:y|ic)|"
    r"radiograph(?:y|ic)|x[- ]?ray|biopsy|histopatholog(?:y|ic)|"
    r"immunoassay|fluorescence immunoassay|antibody tests?|antibody titers?|"
    r"scintigraph(?:y|ic)|pet[- ]?ct|genetic test(?:ing)?|"
    r"molecular test(?:ing)?|culture results?|serum levels?|"
    r"blood levels?|imaging showed|scan showed)\b",
    re.IGNORECASE,
)

# ``case_report`` is not guaranteed to be a pre-test history. RareArena rows
# can begin with laboratory methods or can append imaging/pathology results to
# the clinical narrative. Experience retrieval must use only the text before
# the first such marker. Keep this separate from GoS input: GoS still receives
# the benchmark's full case report and test-results fields.
_RETRIEVAL_CUTOFF_RE = re.compile(
    r"\b(?:"
    r"written informed consent|informed,? written consent|ethical committee|ethics committee|"
    r"study was approved|study received approval|approved by the ethical|"
    r"national bioethics committee|"
    r"underwent\b.{0,80}\btests?|investigations?\b|"
    r"blood gas analysis|blood ammonia (?:testing|level)|"
    r"routine blood samples?|blood samples? (?:taken|drawn|collected)|"
    r"blood and urine tests?|routine blood and urine tests?|routine urine tests?|"
    r"urine tests? (?:showed|were)|"
    r"initial blood chemistry|blood chemistry results?|blood count results?|"
    r"epworth sleepiness scale|\bess\s+score\b|"
    r"diagnosed with|diagnosis of|known diagnosis of|"
    r"(?:obstructive|central) sleep apnea|"
    r"for the management of|management of (?:vesicoureteral|"
    r"gastroesophageal|gastro-?esophageal)|"
    r"initial(?: clinical)? assessment suggested|initially thought to be|"
    r"was thought to be|"
    r"managed as presumptive|presumed diagnosis|suspected diagnosis|"
    r"began treatment with|started treatment with|initiated treatment with|"
    r"started taking|began taking|testing positive for|tested positive for|"
    r"thyroid peroxidase autoantibodies|autoantibodies|"
    r"genetic analysis revealed|genetic analysis showed|genetic testing revealed|"
    r"(?:uncorrected\s+)?(?:distance\s+)?visual acu(?:ity|ities)"
    r"(?:\s*\([^)]+\))?\s+(?:of|was|were|measured|recorded|reduced)|"
    r"plasma [\w-]+ profiling|urinary organic acid|"
    r"intravenous correction|potassium levels?|"
    r"complete blood count|\bcbc\b|platelet count|echocardiogram|"
    r"thrombocytopenia|platelet counts?\s+(?:were|was|of)|"
    r"ophthalmologic evaluation|retina showed|"
    r"best[- ]corrected visual acu(?:ity|ities)|\bbcva\b|"
    r"visual acu(?:ity|ities)\s+(?:of|was|were|measured|recorded)|"
    r"visual field test|psychological tests?|psychometric tests?|rorschach|\bmmpi\b|"
    r"snellen(?:'s)? chart|visual field (?:testing|examination)|"
    r"bayley scales?|developmental index|cognitive developmental index|"
    r"motor developmental index|standardized (?:developmental )?tests?|"
    r"hematological tests?|haematological tests?|blood tests? showed|"
    r"current medications?|medications? (?:included|were|are)|"
    r"direct immunofluorescence|comprehensive metabolic panel|metabolic panel|"
    r"immunoassay|fluorescence immunoassay|antibody tests?|antibody titers?|"
    r"\bigg antibodies?\b|\bigm antibodies?\b|"
    r"pemphigoid circulating antibodies|\bbpag\s*180\b|\bbpag\s*230\b|"
    r"antinuclear antibodies|\bana\b|\bc3\s+and\s+c4\b|"
    r"colonoscopy|endoscop(?:y|ies)|"
    r"left ventricular ejection fraction|\blvef\b|vital capacity|"
    r"sniff nasal inspiratory pressure|maximal inspiratory pressure|"
    r"hormonal assessment|hormone levels?|gonadotropin levels?|"
    r"hormonal assays?|hormone assays?|thorough workup|diagnostic workup|"
    r"intraocular pressure\s+(?:was|measured|of)|\biop\b|"
    r"urine dipstick|dipstick test|bilirubin\s*(?:\(|levels?|concentration)|"
    r"thyroid and liver function tests?|growth hormone deficiency|bone age|"
    r"recombinant human growth hormone|pulmonary function tests?|"
    r"unified dystonia rating scale|\budrs\b|nephrectomy|kidney.{0,30}removed|"
    r"hypocalcemia|serum parathormone|calcium infusions?|calcium levels?|"
    r"random blood sugar|random glucose|"
    r"\btb\s+levels?|uric acid|urate levels?|tumor biomarker|tumour biomarker|"
    r"tumor marker|tumour marker|tumor markers?|tumour markers?|"
    r"prostate-specific antigen|\bpsa\s+level\b|urine specific gravity|specific gravity of the urine|"
    r"hemoglobin level|hematocrit|haematocrit|"
    r"brain tumour|brain tumor|"
    r"incidental mass|renal mass discovered|mass discovered during evaluation|"
    r"general blood chemistry|blood counts? were|serum catecholamine|"
    r"24[- ]hour urine|urine collection|urinary catecholamine|"
    r"laboratory values?|tried various treatments?|tried multiple treatments?|"
    r"\bmmse\b|\bhmse\b|mini[- ]mental|intelligence quotient|iq score|"
    r"\b(?:serum|plasma|blood)\s+(?:glucose|creatinine|ammonia|lactate|"
    r"potassium|sodium|calcium|phosphate|protein|levels?)\b|"
    r"\bhba1c\b|hemoglobin a1c|proteinuria|"
    r"laboratory (?:test|result|investigation)|lab results?|blood tests?|"
    r"computed tomography|ct scan|ct radiograms?|magnetic resonance imaging|\bmri\b|"
    r"panoramic tomography|panoramic x[- ]?ray|cone beam tomography|tomography|"
    r"x[- ]rays?|radiographic|"
    r"sonogram|ultrasonograph(?:y|ic)|occlusal view|"
    r"electroencephal(?:ogram|ography)|\beeg\b|electrocardiogram|"
    r"\becg\b|\bekg\b|ultrasound|sonograph(?:y|ic)|radiograph(?:y|ic)|"
    r"x[- ]?ray|radiograph(?:s|y|ic)?|orthopantomogram|\bopg\b|"
    r"panoramic (?:dental )?(?:image|radiograph|x[- ]?ray)|"
    r"biopsy|histopatholog(?:y|ic)|histolog(?:y|ic)|"
    r"genetic sequenc(?:e|ing)|chromosome analysis|molecular test(?:ing)?|gene panel|"
    r"polymerase chain reaction|\bpcr\b|karyotype|chromosomal microarray|"
    r"slit[- ]lamp|funduscop(?:y|ic)|ophthalmoscop(?:y|ic)|dermoscop(?:y|ic)|"
    r"trichoscop(?:y|ic)|audiometr(?:y|ic)|echocardiograph(?:y|ic)|"
    r"ent evaluation|hearing test|conductive hearing loss documented|"
    r"electromyograph(?:y|ic)|nerve conduction|angiograph(?:y|ic)|"
    r"endoscop(?:y|ic)|lumbar puncture|cerebrospinal fluid|\bcsf\b|immunohistochem(?:istry|ical)|"
    r"culture (?:result|grew|was positive)|serolog(?:y|ic|ical)|"
    r"diagnostic tests?\b|tests?\s+(?:included|showed|revealed|were performed)|"
    r"(?:test|investigation) results?\b|investigations?\s+(?:showed|revealed|included)|"
    r"the results? (?:showed|revealed)|imaging showed|scan showed|"
    r"mammograph(?:y|ic)|positron emission tomography|\bpet[- ]?ct\b|"
    r"flow cytometr(?:y|ic)|bone marrow (?:biopsy|aspirate)|"
    r"partial gut obstruction|bowel obstruction|stabilization efforts|"
    r"stabilized at the outside|after starting antibiotics|"
    r"subside with antibiotic treatment|improved with antibiotic treatment|"
    r"endoscopies? (?:were )?planned|planned (?:upper|lower) endoscopies|"
    r"\busg\b|\bcpap\b|continuous positive airway pressure|"
    r"pathology report|postoperatively|postoperative|radiotherapy|radiation therapy|"
    r"subtotal resection|surgical resection|follow[- ]?up|preoperative|"
    r"surgically removed|surgically treated|surgical removal|surgical excision|drainage procedure|"
    r"irrigation and debridement|incision and drainage|skin graft|"
    r"underwent surgery|excision|aspiration|ablation|laparotomy|"
    r"previously treated|had been treated|after treatment with|"
    r"treated with|treated (?:her|him|the patient) with|treatment cycles?|"
    r"failed to respond to|did not improve with|has been using|using topical|"
    r"empirical (?:antibiotic|antimicrobial) therapy|"
    r"(?:antibiotic|antimicrobial|fluid) therapy (?:was )?(?:initiated|started)|"
    r"started on (?:antibiotics|antimicrobial|empirical treatment)|"
    r"cardiopulmonary resuscitation|\bcpr\b|defibrillation|"
    r"laser treatment|laser therapy|photocoagulation|"
    r"norepinephrine|amiodarone|intubation|therapeutic hypothermia|extubated|"
    r"received intravenous therapy|was administered|administered intravenous|"
    r"required a tracheotomy|required mechanical ventilation|corrective heart surgery|"
    r"treated for bipolar|"
    r"taking riluzole|"
    r"imaging\b|"
    r"urine organic acid|serum levels?\b|plasma levels?\b"
    r"|(?:underwent|performed|received)\b.{0,80}\b(?:tests?|investigations?)\b"
    r")\b",
    re.IGNORECASE,
)
_INITIAL_CLINICAL_CUE_RE = re.compile(
    r"\b(?:presented with|presented to|complain(?:ed|s)? of|"
    r"reported|experienced|developed|onset|clinical finding|symptom|"
    r"pain|rash|lesion|papule|plaque|seizure|weakness|paralysis|"
    r"visual|vision|hearing|swelling|fever|cough|dyspnea|shortness of breath|"
    r"discoloration|difficulty|abnormality|abnormalities|alopecia|"
    r"developmental delay|growth|redness|itch(?:y|ing)|nodule|mass|"
    r"dizziness|fainting|fracture|vomiting|diarrh(?:ea|oea)|"
    r"hypertension|hypotonia|regurgitation|bleeding|fatigue|"
    r"development|developmental|delay|delayed|behavior|cognitive|"
    r"convulsion|stridor|dysmorph|contracture|deformity|complication|eruption|tooth|dental)\b",
    re.IGNORECASE,
)


def pretest_retrieval_text(case_report: str) -> dict[str, Any]:
    """Return a conservative, auditable pre-test prefix for experience matching."""
    raw = str(case_report or "")
    matches = list(_RETRIEVAL_CUTOFF_RE.finditer(raw))
    marker = min(matches, key=lambda match: match.start()) if matches else None
    end = marker.start() if marker else len(raw)
    prefix = raw[:end]
    if marker is not None:
        from traceevo.rag.medical_knowledge.splitter import sentence_spans

        # Do not retain a dangling clause such as "Routine" or half of a
        # sentence that already contains a post-test result.
        # Determine sentence boundaries on the full text: a prefix can end
        # inside a decimal or abbreviation and falsely look sentence-final.
        sentence_ends = [stop for _start, stop in sentence_spans(raw)
                         if stop <= marker.start()]
        end = max(sentence_ends, default=0)
        prefix = raw[:end]
    text = re.sub(r"\s+", " ", prefix).strip(" \t\r\n;,:-")
    return {
        "text": text,
        "cut_char": end,
        "cut_marker": marker.group(0) if marker else "",
        "truncated": marker is not None,
    }


def prepare_clean_retrieval_split(
    source: Path,
    prepared_dir: Path,
    out_dir: Path,
    *,
    query_n: int = 10,
    seed: int = 20261009,
    knowledge_dir: Path | None = None,
) -> dict[str, Any]:
    """Build clean presentation-only retrieval inputs for a sealed build split.

    The GoS build CSV is left untouched. This creates separate, conservative
    symptom/history prefixes for experience matching and a new source/disease-
    disjoint query holdout. Previously inspected query IDs are excluded.
    """
    if query_n <= 0:
        raise ValueError("query_n must be positive")
    output_files = [
        out_dir / "retrieval_build_features_clean.jsonl",
        out_dir / "retrieval_queries_clean_blinded.jsonl",
        out_dir / "clean_retrieval_manifest.json",
    ]
    if any(path.exists() for path in output_files):
        raise FileExistsError("clean retrieval split artifacts already exist")

    rows = _load_jsonl(source)
    by_id = {str(row.get("_id") or ""): row for row in rows}
    build_map_path = prepared_dir / "build_case_map.jsonl"
    build_map = _load_jsonl(build_map_path)
    if not build_map:
        raise ValueError("build case map is empty")
    selected_build = []
    used_articles: set[str] = set()
    used_diseases: set[str] = set()
    for entry in build_map:
        source_id = str(entry.get("source_record_id") or "")
        source_row = by_id.get(source_id)
        if source_row is None:
            raise ValueError(f"build source record is missing: {source_id}")
        summary = pretest_retrieval_text(str(source_row.get("case_report") or ""))
        clean_text = summary["text"]
        reason = ""
        # A build summary may be a short but valid presenting feature, such as
        # a one-sentence symptom complaint. Query holdouts keep the stricter
        # 80-character floor below.
        if len(clean_text) < 50:
            reason = "short_pretest_prefix"
        elif not _INITIAL_CLINICAL_CUE_RE.search(clean_text):
            reason = "no_initial_clinical_cue"
        elif _RETRIEVAL_CUTOFF_RE.search(clean_text) or _EARLY_TEST_RE.search(clean_text):
            reason = "post_test_marker_remains"
        else:
            clone = dict(source_row, case_report=clean_text, test_results="")
            if _leak_reasons(clone):
                reason = "gold_label_overlap"
        article = _article_id(source_id)
        disease = str(source_row.get("Orpha_id") or "")
        if article in used_articles or disease in used_diseases:
            raise ValueError("existing build split is not source/disease-disjoint")
        used_articles.add(article)
        used_diseases.add(disease)
        selected_build.append({
            "case_index": int(entry["case_index"]),
            "source_record_id": source_id,
            "retrieval_text": clean_text if not reason else "",
            "retrieval_eligible": not bool(reason),
            "retrieval_exclusion_reason": reason,
            "cut_char": summary["cut_char"],
            "cut_marker": summary["cut_marker"],
            "truncated": summary["truncated"],
            "retrieval_text_sha256": hashlib.sha256(
                clean_text.encode("utf-8")).hexdigest(),
        })

    # Avoid reusing any query presented in the first, now-invalid pilot.
    previous_manifest = _json_manifest = {}
    previous_manifest_path = prepared_dir / "selection_manifest.json"
    if previous_manifest_path.is_file():
        _json_manifest = json.loads(previous_manifest_path.read_text(encoding="utf-8"))
        previous_manifest = _json_manifest
    previously_inspected = set(map(str, previous_manifest.get("selected_query_ids", [])))
    for prior_query_path in out_dir.parent.glob(
        "prepared_clean_*/retrieval_queries_clean_blinded.jsonl"
    ):
        previously_inspected.update(
            str(item.get("source_record_id") or "")
            for item in _load_jsonl(prior_query_path)
        )
    knowledge_articles = _knowledge_article_ids(knowledge_dir)
    exclusion_counts: Counter[str] = Counter()
    candidates: list[dict[str, Any]] = []
    for row in rows:
        source_id = str(row.get("_id") or "").strip()
        if not source_id or source_id in previously_inspected:
            exclusion_counts["previously_inspected_query"] += 1
            continue
        if source_id in _MANUAL_RETRIEVAL_EXCLUSIONS:
            exclusion_counts["manual_presentation_curation"] += 1
            continue
        if not str(row.get("diagnosis") or "").strip() or not str(row.get("Orpha_id") or "").strip():
            exclusion_counts["missing_label_or_orpha_id"] += 1
            continue
        article = _article_id(source_id)
        disease = str(row.get("Orpha_id") or "")
        if article in used_articles:
            exclusion_counts["build_article_overlap"] += 1
            continue
        if disease in used_diseases:
            exclusion_counts["build_disease_overlap"] += 1
            continue
        if article in knowledge_articles:
            exclusion_counts["medical_knowledge_article_overlap"] += 1
            continue
        summary = pretest_retrieval_text(str(row.get("case_report") or ""))
        clean_text = summary["text"]
        if len(clean_text) < 80:
            exclusion_counts["short_pretest_prefix"] += 1
            continue
        if not _INITIAL_CLINICAL_CUE_RE.search(clean_text):
            exclusion_counts["no_initial_clinical_cue"] += 1
            continue
        if _RETRIEVAL_CUTOFF_RE.search(clean_text) or _EARLY_TEST_RE.search(clean_text):
            exclusion_counts["post_test_marker_remains"] += 1
            continue
        clone = dict(row, case_report=clean_text, test_results="")
        if _leak_reasons(clone):
            exclusion_counts["gold_label_overlap"] += 1
            continue
        candidates.append({
            "row": row,
            "retrieval_text": clean_text,
            "cut_char": summary["cut_char"],
            "cut_marker": summary["cut_marker"],
            "truncated": summary["truncated"],
        })

    ordered = list(candidates)
    random.Random(seed).shuffle(ordered)
    selected_queries = []
    seen_articles = set(used_articles)
    seen_diseases = set(used_diseases)
    for candidate in ordered:
        row = candidate["row"]
        source_id = str(row["_id"])
        article = _article_id(source_id)
        disease = str(row["Orpha_id"])
        if article in seen_articles or disease in seen_diseases:
            continue
        selected_queries.append(candidate)
        seen_articles.add(article)
        seen_diseases.add(disease)
        if len(selected_queries) == query_n:
            break
    if len(selected_queries) != query_n:
        raise ValueError(
            f"need {query_n} clean source/disease-disjoint queries; "
            f"found {len(selected_queries)} from {len(candidates)} candidates"
        )

    build_features = selected_build
    clean_queries = []
    query_gold = []
    for index, candidate in enumerate(selected_queries):
        row = candidate["row"]
        query_id = f"rarecase-clean-query-{index:03d}"
        clean_queries.append({
            "query_id": query_id,
            "source_record_id": str(row["_id"]),
            "initial_presentation": candidate["retrieval_text"],
            "cut_marker": candidate["cut_marker"],
        })
        query_gold.append({
            "query_id": query_id,
            "source_record_id": str(row["_id"]),
            "diagnosis": str(row["diagnosis"]),
            "orpha_id": str(row["Orpha_id"]),
            "orpha_name": str(row["Orpha_name"]),
        })

    out_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(out_dir / "retrieval_build_features_clean.jsonl", build_features)
    _write_jsonl(out_dir / "retrieval_queries_clean_blinded.jsonl", clean_queries)
    labels_dir = out_dir / "label_vault"
    labels_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(labels_dir / "clean_query_gold.jsonl", query_gold)
    manifest = {
        "dataset": "RareArena RDC benchmark",
        "source_url": SOURCE_URL,
        "source_sha256": _sha256(source),
        "source_license": LICENSE,
        "build_cases": len(build_features),
        "clean_query_cases": len(clean_queries),
        "seed": seed,
        "pretest_extractor": "rarecase-pretest-prefix-v21",
        "cutoff_policy": (
            "Keep only case_report text before the first explicit test/imaging/"
            "pathology/genetic/specialist-procedure marker; require at least 50 "
            "characters for build summaries and 80 for query summaries plus an "
            "initial clinical cue; reject any retained marker, "
            "gold-label n-gram overlap, repeated article, repeated Orpha ID, "
            "previously inspected query, or PMC corpus article."),
        "build_source_ids": [row["source_record_id"] for row in build_features],
        "clean_query_source_ids": [row["source_record_id"] for row in clean_queries],
        "excluded_counts": dict(exclusion_counts),
        "manual_retrieval_exclusions": _MANUAL_RETRIEVAL_EXCLUSIONS,
        "build_features_sha256": _sha256(out_dir / "retrieval_build_features_clean.jsonl"),
        "clean_queries_sha256": _sha256(out_dir / "retrieval_queries_clean_blinded.jsonl"),
        "clean_query_gold_sha256": _sha256(labels_dir / "clean_query_gold.jsonl"),
        "label_vault_policy": (
            "Gold labels are retained separately for a future retrieval relevance/outcome "
            "audit. They are not provided to the embedder, Qdrant, reranker, or GoS."),
    }
    (out_dir / "clean_retrieval_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")


def _article_id(record_id: str) -> str:
    return record_id.rsplit("-", 1)[0] if "-" in record_id else record_id


def _input_text(row: dict[str, Any]) -> str:
    return "\n".join((str(row.get("case_report") or ""),
                      str(row.get("test_results") or "")))


def _leak_reasons(row: dict[str, Any]) -> list[str]:
    text = _input_text(row)
    folded = text.casefold()
    reasons: list[str] = []
    for field in ("diagnosis", "Orpha_name"):
        label = str(row.get(field) or "").strip()
        if not label:
            reasons.append(f"missing_{field}")
            continue
        if label.casefold() in folded:
            reasons.append(f"exact_{field}")
        elif ngrams(label) & ngrams(text):
            reasons.append(f"reference_5gram_{field}")
    return reasons


def _knowledge_article_ids(knowledge_dir: Path | None) -> set[str]:
    if knowledge_dir is None or not knowledge_dir.is_dir():
        return set()
    ids: set[str] = set()
    for document_path in knowledge_dir.glob("*/document.json"):
        try:
            doc = json.loads(document_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        document_id = str(doc.get("document_id") or "")
        if document_id.startswith("PMC-"):
            ids.add(document_id[4:])
    return ids


def _candidate_rows(
    rows: list[dict[str, Any]],
    knowledge_article_ids: set[str],
) -> tuple[list[dict[str, Any]], Counter[str]]:
    counts: Counter[str] = Counter()
    candidates: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for row in rows:
        record_id = str(row.get("_id") or "").strip()
        case_report = str(row.get("case_report") or "").strip()
        test_results = str(row.get("test_results") or "").strip()
        diagnosis = str(row.get("diagnosis") or "").strip()
        orpha_id = str(row.get("Orpha_id") or "").strip()
        if not record_id or record_id in seen_ids:
            counts["missing_or_duplicate_record_id"] += 1
            continue
        seen_ids.add(record_id)
        if len(case_report) < 250 or len(test_results) < 40:
            counts["insufficient_case_or_test_text"] += 1
            continue
        if not diagnosis or not orpha_id:
            counts["missing_gold_or_orpha_id"] += 1
            continue
        if _article_id(record_id) in knowledge_article_ids:
            counts["source_article_in_medical_knowledge_corpus"] += 1
            continue
        leaks = _leak_reasons(row)
        if leaks:
            counts["gold_or_label_overlap"] += 1
            continue
        if _EARLY_TEST_RE.search(case_report):
            counts["case_report_contains_post_test_findings"] += 1
            continue
        candidates.append(row)
    return candidates, counts


def _select_disjoint(
    candidates: list[dict[str, Any]], *, build_n: int, query_n: int, seed: int
) -> list[dict[str, Any]]:
    requested = build_n + query_n
    ordered = list(candidates)
    random.Random(seed).shuffle(ordered)
    selected: list[dict[str, Any]] = []
    seen_articles: set[str] = set()
    seen_diseases: set[str] = set()
    for row in ordered:
        article = _article_id(str(row["_id"]))
        disease = str(row["Orpha_id"])
        if article in seen_articles or disease in seen_diseases:
            continue
        selected.append(row)
        seen_articles.add(article)
        seen_diseases.add(disease)
        if len(selected) == requested:
            break
    if len(selected) != requested:
        raise ValueError(
            f"need {requested} clean, source/disease-disjoint cases; "
            f"found {len(selected)}"
        )
    return selected


def prepare(
    source: Path,
    out_dir: Path,
    *,
    build_n: int = 20,
    query_n: int = 10,
    seed: int = 20261008,
    knowledge_dir: Path | None = None,
) -> dict[str, Any]:
    if build_n <= 0 or query_n <= 0:
        raise ValueError("build_n and query_n must be positive")
    rows = _load_jsonl(source)
    candidates, exclusion_counts = _candidate_rows(
        rows, _knowledge_article_ids(knowledge_dir)
    )
    selected = _select_disjoint(
        candidates, build_n=build_n, query_n=query_n, seed=seed
    )
    build_rows, query_rows = selected[:build_n], selected[build_n:]
    out_dir.mkdir(parents=True, exist_ok=True)
    labels_dir = out_dir / "label_vault"
    labels_dir.mkdir(parents=True, exist_ok=True)

    build_csv = out_dir / "dataset_blinded_build.csv"
    columns = ["Case Information", "Physical Examination", "Diagnostic Tests"]
    with build_csv.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for row in build_rows:
            writer.writerow({
                "Case Information": str(row["case_report"]).strip(),
                "Physical Examination": "",
                "Diagnostic Tests": str(row["test_results"]).strip(),
            })

    build_map = [
        {"case_index": i, "source_record_id": str(row["_id"]),
         "article_id": _article_id(str(row["_id"])), "split": "build"}
        for i, row in enumerate(build_rows)
    ]
    query_rows_public = [
        {"query_id": f"rarecase-query-{i:03d}",
         "source_record_id": str(row["_id"]),
         "initial_presentation": str(row["case_report"]).strip()}
        for i, row in enumerate(query_rows)
    ]
    _write_jsonl(out_dir / "build_case_map.jsonl", build_map)
    _write_jsonl(out_dir / "retrieval_queries_blinded.jsonl", query_rows_public)

    build_labels = [
        {"case_index": i, "source_record_id": str(row["_id"]),
         "diagnosis": str(row["diagnosis"]),
         "orpha_id": str(row["Orpha_id"]),
         "orpha_name": str(row["Orpha_name"])}
        for i, row in enumerate(build_rows)
    ]
    query_labels = [
        {"query_id": f"rarecase-query-{i:03d}",
         "source_record_id": str(row["_id"]),
         "diagnosis": str(row["diagnosis"]),
         "orpha_id": str(row["Orpha_id"]),
         "orpha_name": str(row["Orpha_name"])}
        for i, row in enumerate(query_rows)
    ]
    _write_jsonl(labels_dir / "build_gold.jsonl", build_labels)
    _write_jsonl(labels_dir / "query_gold.jsonl", query_labels)

    manifest = {
        "dataset": "RareArena RDC benchmark",
        "source_url": SOURCE_URL,
        "source_file": source.name,
        "source_sha256": _sha256(source),
        "source_license": LICENSE,
        "attribution": ATTRIBUTION,
        "source_rows": len(rows),
        "clean_candidates": len(candidates),
        "exclusion_counts": dict(exclusion_counts),
        "split_seed": seed,
        "build_cases": build_n,
        "retrieval_query_cases": query_n,
        "split_rule": (
            "Rows with explicit post-test marker terms in case_report, or "
            "exact gold/Orpha-name or 5-content-token overlap in case/test "
            "text, are excluded. Source article IDs and Orpha IDs are unique "
            "across the complete selected set, so build/query sets are "
            "disjoint by both article and disease."
        ),
        "selected_build_ids": [str(row["_id"]) for row in build_rows],
        "selected_query_ids": [str(row["_id"]) for row in query_rows],
        "blinded_build_csv_sha256": _sha256(build_csv),
        "build_map_sha256": _sha256(out_dir / "build_case_map.jsonl"),
        "retrieval_queries_sha256": _sha256(out_dir / "retrieval_queries_blinded.jsonl"),
        "build_gold_sha256": _sha256(labels_dir / "build_gold.jsonl"),
        "query_gold_sha256": _sha256(labels_dir / "query_gold.jsonl"),
        "label_vault_policy": (
            "label_vault is for the separate post-seal outcome verifier and "
            "retrieval evaluation only; never pass it to GoS, a planner, or "
            "the experience embedding/reranking query."
        ),
    }
    manifest_path = out_dir / "selection_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def prepare_independent_cohort(
    source: Path,
    out_dir: Path,
    *,
    previous_prepared_dirs: list[Path],
    build_n: int = 20,
    query_n: int = 10,
    seed: int = 20261010,
    knowledge_dir: Path | None = None,
) -> dict[str, Any]:
    """Create a new source/disease-disjoint build and clean-query cohort.

    Prior build/query IDs are collected from the original split and every
    ``prepared_clean_*`` split beside it. The source GoS cases keep their full
    benchmark inputs; the separate experience feature and query files contain
    only a conservative pre-test prefix.
    """
    if build_n <= 0 or query_n <= 0:
        raise ValueError("build_n and query_n must be positive")
    output_files = (
        "dataset_blinded_build.csv", "build_case_map.jsonl",
        "retrieval_build_features_clean.jsonl", "retrieval_queries_clean_blinded.jsonl",
        "selection_manifest.json",
    )
    if any((out_dir / name).exists() for name in output_files):
        raise FileExistsError("independent cohort artifacts already exist")

    rows = _load_jsonl(source)
    by_id = {str(row.get("_id") or ""): row for row in rows}
    prior_ids: set[str] = set()
    prior_articles: set[str] = set()
    prior_diseases: set[str] = set()

    def add_prior_ids(values: list[Any]) -> None:
        for value in values:
            source_id = str(value or "").strip()
            if not source_id:
                continue
            prior_ids.add(source_id)
            prior_articles.add(_article_id(source_id))
            prior_row = by_id.get(source_id)
            if prior_row is not None and prior_row.get("Orpha_id"):
                prior_diseases.add(str(prior_row["Orpha_id"]))

    scanned_dirs: set[Path] = set()
    for prepared_dir in previous_prepared_dirs:
        prepared_dir = prepared_dir.resolve()
        if prepared_dir in scanned_dirs:
            continue
        scanned_dirs.add(prepared_dir)
        selection = prepared_dir / "selection_manifest.json"
        if selection.is_file():
            manifest = json.loads(selection.read_text(encoding="utf-8"))
            add_prior_ids(list(manifest.get("selected_build_ids", [])))
            add_prior_ids(list(manifest.get("selected_query_ids", [])))
        for filename in ("build_case_map.jsonl", "retrieval_queries_blinded.jsonl"):
            path = prepared_dir / filename
            if path.is_file():
                add_prior_ids([row.get("source_record_id") for row in _load_jsonl(path)])
        clean_manifest = prepared_dir / "clean_retrieval_manifest.json"
        if clean_manifest.is_file():
            manifest = json.loads(clean_manifest.read_text(encoding="utf-8"))
            add_prior_ids(list(manifest.get("build_source_ids", [])))
            add_prior_ids(list(manifest.get("clean_query_source_ids", [])))
        for clean_dir in prepared_dir.parent.glob("prepared_clean_*"):
            clean_manifest = clean_dir / "clean_retrieval_manifest.json"
            if not clean_manifest.is_file():
                continue
            manifest = json.loads(clean_manifest.read_text(encoding="utf-8"))
            add_prior_ids(list(manifest.get("build_source_ids", [])))
            add_prior_ids(list(manifest.get("clean_query_source_ids", [])))

    knowledge_articles = _knowledge_article_ids(knowledge_dir)
    exclusion_counts: Counter[str] = Counter()
    build_candidates: list[dict[str, Any]] = []
    query_candidates: list[dict[str, Any]] = []
    for row in rows:
        source_id = str(row.get("_id") or "").strip()
        case_report = str(row.get("case_report") or "").strip()
        test_results = str(row.get("test_results") or "").strip()
        diagnosis = str(row.get("diagnosis") or "").strip()
        orpha_id = str(row.get("Orpha_id") or "").strip()
        if not source_id or not diagnosis or not orpha_id:
            exclusion_counts["missing_id_or_label"] += 1
            continue
        if source_id in _MANUAL_RETRIEVAL_EXCLUSIONS:
            exclusion_counts["manual_presentation_curation"] += 1
            continue
        if source_id in prior_ids:
            exclusion_counts["previously_used_record"] += 1
            continue
        article = _article_id(source_id)
        if article in prior_articles:
            exclusion_counts["previously_used_article"] += 1
            continue
        if orpha_id in prior_diseases:
            exclusion_counts["previously_used_disease"] += 1
            continue
        if article in knowledge_articles:
            exclusion_counts["medical_knowledge_article_overlap"] += 1
            continue
        if len(case_report) < 250 or len(test_results) < 40:
            exclusion_counts["insufficient_case_or_test_text"] += 1
            continue
        if _leak_reasons(row):
            exclusion_counts["source_input_gold_label_overlap"] += 1
            continue
        summary = pretest_retrieval_text(case_report)
        clean_text = summary["text"]
        if len(clean_text) < 50:
            exclusion_counts["short_pretest_prefix"] += 1
            continue
        if not _INITIAL_CLINICAL_CUE_RE.search(clean_text):
            exclusion_counts["no_initial_clinical_cue"] += 1
            continue
        if _RETRIEVAL_CUTOFF_RE.search(clean_text) or _EARLY_TEST_RE.search(clean_text):
            exclusion_counts["post_test_marker_remains"] += 1
            continue
        if _leak_reasons(dict(row, case_report=clean_text, test_results="")):
            exclusion_counts["gold_label_overlap"] += 1
            continue

        candidate = {
            "row": row,
            "retrieval_text": clean_text,
            "cut_char": summary["cut_char"],
            "cut_marker": summary["cut_marker"],
            "truncated": summary["truncated"],
        }
        build_candidates.append(candidate)
        if len(clean_text) >= 80:
            query_candidates.append(candidate)
        else:
            exclusion_counts["query_prefix_under_80_chars"] += 1

    rng = random.Random(seed)
    rng.shuffle(build_candidates)
    rng.shuffle(query_candidates)
    selected_build: list[dict[str, Any]] = []
    selected_queries: list[dict[str, Any]] = []
    seen_articles = set(prior_articles)
    seen_diseases = set(prior_diseases)

    def select(candidates: list[dict[str, Any]], target: list[dict[str, Any]], count: int) -> None:
        for candidate in candidates:
            row = candidate["row"]
            article = _article_id(str(row["_id"]))
            disease = str(row["Orpha_id"])
            if article in seen_articles or disease in seen_diseases:
                continue
            target.append(candidate)
            seen_articles.add(article)
            seen_diseases.add(disease)
            if len(target) == count:
                return
        if len(target) != count:
            raise ValueError(
                f"need {count} more clean independent cases; found {len(target)}"
            )

    select(build_candidates, selected_build, build_n)
    select(query_candidates, selected_queries, query_n)

    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "dataset_blinded_build.csv").open(
        "w", newline="", encoding="utf-8"
    ) as stream:
        writer = csv.DictWriter(
            stream, fieldnames=["Case Information", "Physical Examination", "Diagnostic Tests"]
        )
        writer.writeheader()
        for candidate in selected_build:
            row = candidate["row"]
            writer.writerow({
                "Case Information": str(row["case_report"]).strip(),
                "Physical Examination": "",
                "Diagnostic Tests": str(row["test_results"]).strip(),
            })

    build_map = []
    build_features = []
    build_gold = []
    for index, candidate in enumerate(selected_build):
        row = candidate["row"]
        source_id = str(row["_id"])
        build_map.append({
            "case_index": index,
            "source_record_id": source_id,
            "article_id": _article_id(source_id),
            "split": "independent_build",
        })
        clean_text = candidate["retrieval_text"]
        build_features.append({
            "case_index": index,
            "source_record_id": source_id,
            "retrieval_text": clean_text,
            "retrieval_eligible": True,
            "retrieval_exclusion_reason": "",
            "cut_char": candidate["cut_char"],
            "cut_marker": candidate["cut_marker"],
            "truncated": candidate["truncated"],
            "retrieval_text_sha256": hashlib.sha256(clean_text.encode("utf-8")).hexdigest(),
        })
        build_gold.append({
            "case_index": index,
            "source_record_id": source_id,
            "diagnosis": str(row["diagnosis"]),
            "orpha_id": str(row["Orpha_id"]),
            "orpha_name": str(row["Orpha_name"]),
        })

    clean_queries = []
    query_gold = []
    for index, candidate in enumerate(selected_queries):
        row = candidate["row"]
        query_id = f"rarecase-independent-query-{index:03d}"
        clean_queries.append({
            "query_id": query_id,
            "source_record_id": str(row["_id"]),
            "initial_presentation": candidate["retrieval_text"],
            "cut_marker": candidate["cut_marker"],
        })
        query_gold.append({
            "query_id": query_id,
            "source_record_id": str(row["_id"]),
            "diagnosis": str(row["diagnosis"]),
            "orpha_id": str(row["Orpha_id"]),
            "orpha_name": str(row["Orpha_name"]),
        })

    _write_jsonl(out_dir / "build_case_map.jsonl", build_map)
    _write_jsonl(out_dir / "retrieval_build_features_clean.jsonl", build_features)
    _write_jsonl(out_dir / "retrieval_queries_clean_blinded.jsonl", clean_queries)
    labels_dir = out_dir / "label_vault"
    labels_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(labels_dir / "build_gold.jsonl", build_gold)
    _write_jsonl(labels_dir / "query_gold.jsonl", query_gold)

    manifest = {
        "dataset": "RareArena RDC benchmark",
        "source_url": SOURCE_URL,
        "source_file": source.name,
        "source_sha256": _sha256(source),
        "source_license": LICENSE,
        "attribution": ATTRIBUTION,
        "source_rows": len(rows),
        "prior_used_records": len(prior_ids),
        "prior_used_articles": len(prior_articles),
        "prior_used_diseases": len(prior_diseases),
        "split_seed": seed,
        "build_cases": build_n,
        "retrieval_query_cases": query_n,
        "split_rule": (
            "New source records, article IDs, and Orpha IDs are disjoint from all prior "
            "prepared and prepared_clean_* splits; new build and query subsets are also "
            "source/article/disease-disjoint. Experience keys and queries contain only "
            "the conservative pre-test case_report prefix."
        ),
        "selected_build_ids": [row["source_record_id"] for row in build_map],
        "selected_query_ids": [row["source_record_id"] for row in clean_queries],
        "build_source_ids": [row["source_record_id"] for row in build_map],
        "clean_query_source_ids": [row["source_record_id"] for row in clean_queries],
        "exclusion_counts": dict(exclusion_counts),
        "blinded_build_csv_sha256": _sha256(out_dir / "dataset_blinded_build.csv"),
        "build_map_sha256": _sha256(out_dir / "build_case_map.jsonl"),
        "build_features_sha256": _sha256(out_dir / "retrieval_build_features_clean.jsonl"),
        "clean_queries_sha256": _sha256(out_dir / "retrieval_queries_clean_blinded.jsonl"),
        "build_gold_sha256": _sha256(labels_dir / "build_gold.jsonl"),
        "query_gold_sha256": _sha256(labels_dir / "query_gold.jsonl"),
        "label_vault_policy": (
            "Gold labels remain separate from GoS, experience embeddings, Qdrant, and "
            "retrieval queries."
        ),
    }
    (out_dir / "selection_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--build-n", type=int, default=20)
    parser.add_argument("--query-n", type=int, default=10)
    parser.add_argument("--seed", type=int, default=20261008)
    parser.add_argument(
        "--knowledge-dir", type=Path,
        default=Path("traceevo_runs/rag/medical_knowledge/normalized/schema-v5"),
    )
    args = parser.parse_args()
    manifest = prepare(
        args.source,
        args.out,
        build_n=args.build_n,
        query_n=args.query_n,
        seed=args.seed,
        knowledge_dir=args.knowledge_dir,
    )
    print(json.dumps({
        "source_rows": manifest["source_rows"],
        "clean_candidates": manifest["clean_candidates"],
        "build_cases": manifest["build_cases"],
        "retrieval_query_cases": manifest["retrieval_query_cases"],
        "out": str(args.out),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
