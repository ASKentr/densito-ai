"""Отчёт DICOM SR (ai_module/dicom_sr.py, ТЗ п.2.6): структура документа,
воспроизводимость UID, выгрузка в пакетной обработке и в веб-API."""
import io
import zipfile
from pathlib import Path

import pydicom
import pytest

from ai_module.batch import result_zip_bytes, run_batch, sr_member_name, write_sr_files
from ai_module.dicom_sr import BASIC_TEXT_SR, SRContent, SRFinding, build_sr, sr_bytes, sr_text
from tests.test_batch import OK_HIP, OK_SPINE, SAMPLES, _make_archive, api_client  # noqa: F401 — фикстура

MISSING_TAGS = SAMPLES / "08_lspine_missing_tags.dcm"

CONTENT = SRContent(
    region="Поясничный отдел позвоночника", quality_class=1, quality_prob=0.71,
    model_version="test-0.1",
    findings=[SRFinding("Не выравнена ось позвоночника", "Наклон оси 7.2° при допуске 5°.")],
)


def _roundtrip(ds) -> pydicom.Dataset:
    return pydicom.dcmread(io.BytesIO(sr_bytes(ds)))


def test_sr_structure_and_reference_to_source():
    src = pydicom.dcmread(OK_SPINE)
    sr = _roundtrip(build_sr(src, CONTENT))

    assert sr.SOPClassUID == BASIC_TEXT_SR and sr.file_meta.MediaStorageSOPClassUID == BASIC_TEXT_SR
    assert sr.Modality == "SR" and sr.ValueType == "CONTAINER"
    assert sr.StudyInstanceUID == src.StudyInstanceUID            # тот же study, что и снимок
    assert sr.SeriesInstanceUID != src.SeriesInstanceUID           # отдельная серия
    ev = sr.CurrentRequestedProcedureEvidenceSequence[0].ReferencedSeriesSequence[0].ReferencedSOPSequence[0]
    assert ev.ReferencedSOPInstanceUID == src.SOPInstanceUID
    image_items = [i for i in sr.ContentSequence if i.ValueType == "IMAGE"]
    assert image_items[0].ReferencedSOPSequence[0].ReferencedSOPInstanceUID == src.SOPInstanceUID

    text = sr_text(sr)                                            # UTF-8 кириллица читается обратно
    assert "Есть нарушение качества" in text
    assert "Не выравнена ось позвоночника. Наклон оси 7.2° при допуске 5°." in text
    assert "Поясничный отдел позвоночника" in text and "0.71" in text


def test_sr_uid_is_reproducible_and_content_dependent():
    src = pydicom.dcmread(OK_SPINE)
    a, b = build_sr(src, CONTENT), build_sr(src, CONTENT)
    assert a.SOPInstanceUID == b.SOPInstanceUID
    changed = SRContent(**{**CONTENT.__dict__, "review_status": "Проверено экспертом"})
    assert build_sr(src, changed).SOPInstanceUID != a.SOPInstanceUID


def test_sr_without_uids_in_source():
    src = pydicom.dcmread(MISSING_TAGS, force=True)
    for tag in ("StudyInstanceUID", "SOPInstanceUID", "SOPClassUID"):
        if tag in src:
            delattr(src, tag)
    sr = _roundtrip(build_sr(src, SRContent("", 0, None, "test-0.1")))
    assert sr.StudyInstanceUID and "Качественное исследование" in sr_text(sr)
    assert not [i for i in sr.ContentSequence if i.ValueType == "IMAGE"]


def test_batch_with_sr_zip(tmp_path):
    archive = tmp_path / "set.zip"
    _make_archive(archive)
    rows = run_batch(archive, "heuristics", with_sr=True)
    assert all(r.get("sr") for r in rows)
    assert sr_member_name(rows[0]).startswith("set.zip/") and sr_member_name(rows[0]).endswith(".sr.dcm")

    with zipfile.ZipFile(io.BytesIO(result_zip_bytes(rows, "csv"))) as zf:
        names = zf.namelist()
        assert "densito_results.csv" in names
        srs = [n for n in names if n.startswith("sr/")]
        assert len(srs) == 3
        assert pydicom.dcmread(io.BytesIO(zf.read(srs[0]))).Modality == "SR"

    assert write_sr_files(rows, tmp_path / "sr") == 3
    assert len(list((tmp_path / "sr").rglob("*.sr.dcm"))) == 3


def test_batch_without_sr_has_no_sr_field():
    assert "sr" not in run_batch(OK_SPINE, "heuristics")[0]


def test_api_batch_sr_zip(api_client):  # noqa: F811
    with open(OK_HIP, "rb") as f:
        r = api_client.post("/batch?format=xlsx&sr=true", files={"file": ("02_hip_ok.dcm", f, "application/dicom")})
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        assert sorted(zf.namelist()) == ["densito_results.xlsx", "sr/02_hip_ok.dcm.sr.dcm"]


def test_api_study_image_sr(api_client, tmp_path, monkeypatch):  # noqa: F811
    from app.config import settings
    monkeypatch.setattr(settings, "storage_dir", str(tmp_path))
    with open(OK_HIP, "rb") as f:
        study = api_client.post("/studies/upload", files={"file": ("02_hip_ok.dcm", f, "application/dicom")}).json()
    image_id = study["images"][0]["id"]
    assert api_client.get(f"/studies/{study['id']}/images/{image_id}/sr").status_code == 409  # ещё не анализировали

    assert api_client.post(f"/studies/{study['id']}/analyze").status_code == 200
    r = api_client.get(f"/studies/{study['id']}/images/{image_id}/sr")
    assert r.status_code == 200 and r.headers["content-type"] == "application/dicom"
    sr = pydicom.dcmread(io.BytesIO(r.content))
    assert sr.Modality == "SR"
    assert "Экспертная проверка не проводилась" in sr_text(sr)
    assert sr.StudyInstanceUID == pydicom.dcmread(OK_HIP).StudyInstanceUID
