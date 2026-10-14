import os
import pandas as pd

def Process_Diagnosis_Symptoms(cfg_domain, dataset):
    dataset_path = os.path.join("datasets", dataset)
    # TraceEvo S3B: an explicit override for the ground-truth isolation
    # experiment (§4.1). When set, the run reads a copy of the dataset with the
    # `Final Diagnosis` / `Right Option` columns removed, so the gold label is
    # not merely unread during the run -- it is not in the process's inputs at
    # all. Unset, this is the original path.
    data_path = os.environ.get("TRACEEVO_DATASET_CSV") or os.path.join(
        dataset_path, "test_set.csv")
    data = pd.read_csv(data_path)
    blinded = "Final Diagnosis" not in data.columns
    symptoms = []
    evidence_texts = []
    answers = []
    for idx, case in data.iterrows():
        case_info = case["Case Information"]
        physical_info = case["Physical Examination"]
        evidence_text = case["Diagnostic Tests"]
        answer = "" if blinded else case["Final Diagnosis"]
        description = f"##Case information: {case_info}\n## Physical Examination: {physical_info}"
        symptoms.append((idx,description))
        evidence_texts.append(evidence_text)
        answers.append(answer)
    return symptoms, evidence_texts, answers

