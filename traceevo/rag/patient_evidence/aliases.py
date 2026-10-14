"""Deterministic exam-name aliases used for safe structured filtering."""
from __future__ import annotations

EXAM_ALIASES: dict[str, tuple[str, ...]] = {
    "mri": (
        "mri", "mr", "mr imaging", "magnetic resonance", "magnetic resonance imaging",
        "磁共振", "核磁共振",
    ),
    "mrcp": (
        "mrcp", "magnetic resonance cholangiopancreatography", "磁共振胰胆管成像",
    ),
    "cta": (
        "cta", "ct angiography", "computed tomography angiography", "ct血管成像",
    ),
    "ct": (
        "ct", "ct scan", "computed tomography", "computed tomographic",
        "computerized tomography", "计算机断层扫描", "ct检查",
    ),
    "pet_ct": ("pet-ct", "pet ct", "positron emission tomography", "pet-ct检查"),
    "xray": (
        "x-ray", "x ray", "xray", "radiograph", "radiography", "plain film",
        "chest x-ray", "胸片", "x光", "x线", "放射线摄影",
    ),
    "ultrasound": (
        "ultrasound", "ultrasonography", "sonography", "ultrasound scan", "超声",
        "超声检查", "超声波",
    ),
    "fluoroscopy": (
        "fluoroscopy", "fluoroscopic", "modified barium swallow", "barium swallow",
        "透视", "钡餐造影",
    ),
    "oct": (
        "oct", "optical coherence tomography", "光学相干断层扫描", "光学相干断层成像",
    ),
    "ffa": (
        "ffa", "fundus fluorescein angiography", "fluorescein angiography",
        "荧光素眼底血管造影", "眼底荧光造影",
    ),
    "mammography": ("mammography", "mammogram", "乳腺钼靶"),
    "angiography": ("angiography", "angiogram", "血管造影"),
    "biopsy": ("biopsy", "histopathology", "pathology specimen", "活检", "病理切片"),
    "endoscopy": (
        "endoscopy", "endoscopic", "gastroscopy", "colonoscopy", "laryngoscopy",
        "内镜", "胃镜", "结肠镜", "喉镜",
    ),
    "echocardiogram": (
        "echocardiogram", "echocardiography", "cardiac ultrasound", "心脏超声",
        "超声心动图",
    ),
    "ecg": ("ecg", "ekg", "electrocardiogram", "心电图"),
    "blood_culture": ("blood culture", "blood cultures", "血培养"),
    "cbc": (
        "cbc", "complete blood count", "full blood count", "hemogram", "血常规",
    ),
    "urinalysis": ("urinalysis", "urine test", "尿常规", "尿液检查"),
    "blood_test": ("blood test", "laboratory test", "laboratory tests", "lab test", "化验"),
    "imaging": ("imaging studies", "imaging test", "影像学检查"),
    "diagnostic_tests": ("diagnostic tests", "diagnostic test", "检查结果"),
}

MODALITY_BY_EXAM: dict[str, str] = {
    **{name: "imaging" for name in (
        "mri", "mrcp", "cta", "ct", "pet_ct", "xray", "ultrasound", "oct",
        "fluoroscopy", "ffa", "mammography", "angiography", "imaging",
    )},
    **{name: "laboratory" for name in (
        "blood_culture", "cbc", "urinalysis", "blood_test",
    )},
    "biopsy": "pathology",
    "endoscopy": "procedure",
    "echocardiogram": "cardiac",
    "ecg": "cardiac",
    "diagnostic_tests": "unknown",
}

MODALITY_ALIASES: dict[str, tuple[str, ...]] = {
    "imaging": ("imaging", "radiology", "影像", "影像学"),
    "laboratory": ("laboratory", "lab", "laboratory test", "化验", "检验"),
    "pathology": ("pathology", "histology", "病理"),
    "procedure": ("procedure", "endoscopy", "内镜", "操作检查"),
    "cardiac": ("cardiac", "cardiology", "心脏检查"),
    "unknown": ("unknown", "其他", "未分类"),
    "mixed": ("mixed", "multiple", "多种"),
}
