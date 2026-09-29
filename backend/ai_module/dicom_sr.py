"""
Текстовый отчёт о выявленных нарушениях в формате DICOM SR (ТЗ п.2.6,
дополнительный функционал; п.2.7 — zip-архив с дополнительными сериями).

Формат — Basic Text SR (SOP Class 1.2.840.10008.5.1.4.1.1.88.11): дерево
TEXT-элементов под корневым CONTAINER. Документ кладётся в то же исследование,
что и исходный снимок (тот же StudyInstanceUID, новая серия с Modality=SR),
и ссылается на снимок (IMAGE-элемент и CurrentRequestedProcedureEvidenceSequence) —
поэтому PACS/просмотрщик показывает отчёт рядом со снимком.

Данные пациента копируются из исходного файла как есть (исходники уже
обезличены организатором); от их наличия ничего не зависит (ТЗ п.2.4).

UID документа и серии выводятся из UID исходного снимка и содержимого отчёта:
повторный прогон на тех же данных даёт тот же UID (ТЗ п.2.7, воспроизводимость),
изменённый отчёт (например, после экспертной проверки) — новый.

Коды понятий: DCM (111001 Algorithm Name, 111003 Algorithm Version,
123014 Target Region, 121073 Impression, 121071 Finding) и частная схема
99DENSITO для заголовка, вероятности, статуса проверки и примечания.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime

from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.sequence import Sequence
from pydicom.uid import ExplicitVRLittleEndian, PYDICOM_IMPLEMENTATION_UID, generate_uid

BASIC_TEXT_SR = "1.2.840.10008.5.1.4.1.1.88.11"
SR_SERIES_NUMBER = 9001
MANUFACTURER = "Densito-AI"
DISCLAIMER = ("Результат автоматической оценки качества исследования. Не является "
              "диагнозом; решение о повторном исследовании принимает специалист.")

_PRIVATE = "99DENSITO"
TITLE = ("DNST-QC", _PRIVATE, "Отчёт контроля качества денситометрии")
ALGORITHM_NAME = ("111001", "DCM", "Algorithm Name")
ALGORITHM_VERSION = ("111003", "DCM", "Algorithm Version")
TARGET_REGION = ("123014", "DCM", "Target Region")
IMPRESSION = ("121073", "DCM", "Impression")
FINDING = ("121071", "DCM", "Finding")
PROBABILITY = ("DNST-PROB", _PRIVATE, "Вероятность нарушения качества")
REVIEW = ("DNST-REVIEW", _PRIVATE, "Статус экспертной проверки")
SOURCE_IMAGE = ("DNST-SRC", _PRIVATE, "Исходное изображение")
NOTE = ("DNST-NOTE", _PRIVATE, "Примечание")

# Модули Patient / General Study, копируемые из исходного снимка.
_COPIED_TAGS = [
    "PatientName", "PatientID", "PatientBirthDate", "PatientSex",
    "StudyDate", "StudyTime", "ReferringPhysicianName", "StudyID", "AccessionNumber",
    "StudyDescription",
]


@dataclass
class SRFinding:
    name: str             # формулировка нарушения
    detail: str = ""      # пояснение алгоритма / комментарий эксперта


@dataclass
class SRContent:
    region: str                       # официальное название области ("" — не определена)
    quality_class: int                # 0 — качественное, 1 — есть нарушение
    quality_prob: float | None
    model_version: str
    findings: list[SRFinding] = field(default_factory=list)
    review_status: str = ""           # пусто — отчёт только по результату ИИ


def _code(value: str, scheme: str, meaning: str) -> Dataset:
    item = Dataset()
    item.CodeValue = value
    item.CodingSchemeDesignator = scheme
    item.CodeMeaning = meaning
    return item


def _text(concept: tuple[str, str, str], text: str, relationship: str = "CONTAINS") -> Dataset:
    item = Dataset()
    item.RelationshipType = relationship
    item.ValueType = "TEXT"
    item.ConceptNameCodeSequence = Sequence([_code(*concept)])
    item.TextValue = text
    return item


def _image_ref(sop_class: str, sop_instance: str) -> Dataset:
    ref = Dataset()
    ref.ReferencedSOPClassUID = sop_class
    ref.ReferencedSOPInstanceUID = sop_instance
    item = Dataset()
    item.RelationshipType = "CONTAINS"
    item.ValueType = "IMAGE"
    item.ConceptNameCodeSequence = Sequence([_code(*SOURCE_IMAGE)])
    item.ReferencedSOPSequence = Sequence([ref])
    return item


def _uid(*parts: str) -> str:
    return generate_uid(entropy_srcs=["densito-sr", *parts])


def impression_text(content: SRContent) -> str:
    return "Есть нарушение качества" if content.quality_class else "Качественное исследование"


def build_sr(source: Dataset, content: SRContent, now: datetime | None = None) -> Dataset:
    """Документ Basic Text SR по результату анализа одного снимка `source`."""
    now = now or datetime.now()
    src_sop = str(getattr(source, "SOPInstanceUID", "") or "")
    src_class = str(getattr(source, "SOPClassUID", "") or "")
    study_uid = str(getattr(source, "StudyInstanceUID", "") or "") or _uid("study", src_sop)
    digest = hashlib.sha256(json.dumps(
        [content.region, content.quality_class, content.quality_prob, content.model_version,
         [(f.name, f.detail) for f in content.findings], content.review_status],
        ensure_ascii=False).encode("utf-8")).hexdigest()

    ds = Dataset()
    ds.SpecificCharacterSet = "ISO_IR 192"  # UTF-8: русский текст
    ds.SOPClassUID = BASIC_TEXT_SR
    ds.SOPInstanceUID = _uid("instance", src_sop, digest)

    for tag in _COPIED_TAGS:
        ds.setdefault(tag, getattr(source, tag, ""))
    ds.StudyInstanceUID = study_uid

    ds.Modality = "SR"
    ds.SeriesInstanceUID = _uid("series", study_uid)
    ds.SeriesNumber = SR_SERIES_NUMBER
    ds.SeriesDescription = "Densito-AI: контроль качества"
    ds.ReferencedPerformedProcedureStepSequence = Sequence()
    ds.Manufacturer = MANUFACTURER
    ds.SoftwareVersions = content.model_version

    ds.InstanceNumber = 1
    ds.ContentDate = now.strftime("%Y%m%d")
    ds.ContentTime = now.strftime("%H%M%S")
    ds.CompletionFlag = "COMPLETE"
    ds.VerificationFlag = "UNVERIFIED"
    ds.PerformedProcedureCodeSequence = Sequence()
    if src_sop and src_class:
        sop_ref = Dataset()
        sop_ref.ReferencedSOPClassUID = src_class
        sop_ref.ReferencedSOPInstanceUID = src_sop
        series_ref = Dataset()
        series_ref.SeriesInstanceUID = str(getattr(source, "SeriesInstanceUID", "") or _uid("src-series", src_sop))
        series_ref.ReferencedSOPSequence = Sequence([sop_ref])
        evidence = Dataset()
        evidence.StudyInstanceUID = study_uid
        evidence.ReferencedSeriesSequence = Sequence([series_ref])
        ds.CurrentRequestedProcedureEvidenceSequence = Sequence([evidence])

    items = [
        _text(ALGORITHM_NAME, MANUFACTURER, "HAS OBS CONTEXT"),
        _text(ALGORITHM_VERSION, content.model_version, "HAS OBS CONTEXT"),
        _text(TARGET_REGION, content.region or "Не определена"),
        _text(IMPRESSION, impression_text(content)),
    ]
    if content.quality_prob is not None:
        items.append(_text(PROBABILITY, f"{content.quality_prob:.2f}"))
    for f in content.findings:
        items.append(_text(FINDING, f"{f.name}. {f.detail}".strip() if f.detail else f.name))
    if content.review_status:
        items.append(_text(REVIEW, content.review_status))
    if src_sop and src_class:
        items.append(_image_ref(src_class, src_sop))
    items.append(_text(NOTE, DISCLAIMER))

    ds.ValueType = "CONTAINER"
    ds.ConceptNameCodeSequence = Sequence([_code(*TITLE)])
    ds.ContinuityOfContent = "SEPARATE"
    ds.ContentSequence = Sequence(items)

    meta = FileMetaDataset()
    meta.MediaStorageSOPClassUID = ds.SOPClassUID
    meta.MediaStorageSOPInstanceUID = ds.SOPInstanceUID
    meta.TransferSyntaxUID = ExplicitVRLittleEndian
    meta.ImplementationClassUID = PYDICOM_IMPLEMENTATION_UID
    ds.file_meta = meta
    ds.is_little_endian = True
    ds.is_implicit_VR = False
    ds.preamble = b"\x00" * 128
    return ds


def sr_bytes(ds: Dataset) -> bytes:
    from io import BytesIO
    buf = BytesIO()
    ds.save_as(buf, write_like_original=False)
    return buf.getvalue()


def sr_text(ds: Dataset) -> str:
    """Плоский текст отчёта (для логов и тестов)."""
    lines = [ds.ConceptNameCodeSequence[0].CodeMeaning]
    for item in ds.ContentSequence:
        if item.ValueType == "TEXT":
            lines.append(f"{item.ConceptNameCodeSequence[0].CodeMeaning}: {item.TextValue}")
    return "\n".join(lines)
