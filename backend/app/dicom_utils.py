"""Утилиты работы с DICOM: чтение, проверка обезличивания, превью для вьюера."""
from __future__ import annotations

import io
from typing import Any

import numpy as np
import pydicom
from PIL import Image

# Теги, наличие которых в файле означает, что данные НЕ обезличены (DICOM PS3.15 Annex E,
# сокращённый практический список для этой задачи).
PHI_TAGS = {
    "PatientName": "ФИО пациента",
    "PatientBirthDate": "Дата рождения пациента",
    "PatientAddress": "Адрес пациента",
    "PatientTelephoneNumbers": "Телефон пациента",
    "OtherPatientIDs": "Прочие ID пациента",
    "OtherPatientNames": "Прочие имена пациента",
    "ReferringPhysicianName": "ФИО направившего врача",
    "PerformingPhysicianName": "ФИО выполнившего врача",
    "InstitutionName": "Название учреждения",
    "InstitutionAddress": "Адрес учреждения",
}

# Значения, которые считаем "уже обезличенными" даже если тег присутствует
ANONYMIZED_PLACEHOLDERS = {"ANONYMIZED", "ANONYMOUS", "", "REMOVED", "^^^^"}

REQUIRED_TAGS = [
    "Modality", "Rows", "Columns", "BitsAllocated", "PhotometricInterpretation",
    "StudyInstanceUID", "SOPInstanceUID",
]


def read_dicom(path: str) -> pydicom.FileDataset:
    return pydicom.dcmread(path, force=True)


def is_dicom_file(path: str) -> bool:
    try:
        pydicom.dcmread(path, stop_before_pixels=True, force=True)
        return True
    except Exception:
        return False


def check_anonymization(ds: pydicom.FileDataset) -> dict[str, Any]:
    found = []
    for tag, label in PHI_TAGS.items():
        if tag in ds:
            value = str(getattr(ds, tag, "")).strip()
            if value.upper() not in ANONYMIZED_PLACEHOLDERS:
                found.append({"tag": tag, "label": label, "value": value})
    return {"ok": len(found) == 0, "found_phi": found}


def check_required_tags(ds: pydicom.FileDataset) -> dict[str, Any]:
    missing = [tag for tag in REQUIRED_TAGS if tag not in ds or getattr(ds, tag, None) in (None, "")]
    return {"ok": len(missing) == 0, "missing_tags": missing}


def extract_metadata(ds: pydicom.FileDataset) -> dict[str, Any]:
    def g(tag, default=None):
        try:
            v = getattr(ds, tag, default)
            return str(v) if v is not None else default
        except Exception:
            return default

    return {
        "study_date": g("StudyDate"),
        "modality": g("Modality"),
        "body_part": g("BodyPartExamined"),
        "study_description": g("StudyDescription"),
        "rows": int(getattr(ds, "Rows", 0) or 0),
        "columns": int(getattr(ds, "Columns", 0) or 0),
        "sop_instance_uid": g("SOPInstanceUID"),
        "manufacturer": g("Manufacturer"),
    }


def get_pixel_array(ds: pydicom.FileDataset) -> np.ndarray:
    arr = ds.pixel_array.astype(np.float64)
    slope = float(getattr(ds, "RescaleSlope", 1) or 1)
    intercept = float(getattr(ds, "RescaleIntercept", 0) or 0)
    return arr * slope + intercept


def windowed_uint8(arr: np.ndarray, wc: float | None = None, ww: float | None = None) -> np.ndarray:
    """Применяет window center/width и возвращает изображение в 0..255 (для превью/эвристик)."""
    if wc is None or ww is None or ww <= 0:
        lo, hi = np.percentile(arr, [1, 99])
    else:
        lo, hi = wc - ww / 2, wc + ww / 2
    if hi <= lo:
        hi = lo + 1
    out = (arr - lo) / (hi - lo) * 255.0
    return np.clip(out, 0, 255).astype(np.uint8)


def render_preview_png(ds: pydicom.FileDataset, max_size: int = 1024) -> bytes:
    arr = get_pixel_array(ds)
    wc = getattr(ds, "WindowCenter", None)
    ww = getattr(ds, "WindowWidth", None)
    if isinstance(wc, pydicom.multival.MultiValue):
        wc = float(wc[0])
    if isinstance(ww, pydicom.multival.MultiValue):
        ww = float(ww[0])
    wc = float(wc) if wc is not None else None
    ww = float(ww) if ww is not None else None

    img8 = windowed_uint8(arr, wc, ww)
    img = Image.fromarray(img8, mode="L")
    if max(img.size) > max_size:
        img.thumbnail((max_size, max_size))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
