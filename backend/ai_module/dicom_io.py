"""
Лёгкий, самодостаточный слой чтения DICOM для AI-модуля. Специально не
импортирует ничего из backend/app — модуль подготовлен к тому, чтобы в будущем
жить отдельным сервисом (см. интерфейс в interface.py).
"""
from __future__ import annotations

import numpy as np
import pydicom

REQUIRED_TAGS = [
    "Modality", "Rows", "Columns", "BitsAllocated", "PhotometricInterpretation",
    "StudyInstanceUID", "SOPInstanceUID",
]

PHI_TAGS = [
    "PatientName", "PatientBirthDate", "PatientAddress", "PatientTelephoneNumbers",
    "OtherPatientIDs", "OtherPatientNames", "ReferringPhysicianName",
    "PerformingPhysicianName", "InstitutionName", "InstitutionAddress",
]
ANONYMIZED_PLACEHOLDERS = {"ANONYMIZED", "ANONYMOUS", "", "REMOVED", "^^^^"}


def read_dicom(path: str) -> pydicom.FileDataset:
    return pydicom.dcmread(path, force=True)


def check_required_tags(ds: pydicom.FileDataset) -> list[str]:
    missing = []
    for tag in REQUIRED_TAGS:
        if tag not in ds or getattr(ds, tag, None) in (None, ""):
            missing.append(tag)
    if "PixelData" not in ds:
        missing.append("PixelData")
    return missing


def check_phi(ds: pydicom.FileDataset) -> list[str]:
    found = []
    for tag in PHI_TAGS:
        if tag in ds:
            value = str(getattr(ds, tag, "")).strip()
            if value.upper() not in ANONYMIZED_PLACEHOLDERS:
                found.append(tag)
    return found


def get_pixel_array(ds: pydicom.FileDataset) -> np.ndarray:
    arr = ds.pixel_array.astype(np.float64)
    slope = float(getattr(ds, "RescaleSlope", 1) or 1)
    intercept = float(getattr(ds, "RescaleIntercept", 0) or 0)
    return arr * slope + intercept


def auto_windowed_uint8(arr: np.ndarray) -> np.ndarray:
    """Windowing по перцентилям — не зависит от корректности тегов WC/WW,
    поэтому геометрические эвристики (позиционирование/обрезка/артефакты)
    устойчивы даже на исследованиях с проблемной экспозицией."""
    lo, hi = np.percentile(arr, [1, 99])
    if hi <= lo:
        hi = lo + 1
    out = (arr - lo) / (hi - lo) * 255.0
    return np.clip(out, 0, 255).astype(np.uint8)
