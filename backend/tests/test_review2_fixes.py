"""Тесты по второму внешнему ревью (29.09.2026): лимиты загрузки и защита от
«ZIP-бомб», независимость ИИ от метаданных DICOM."""
import io
import zipfile
from pathlib import Path

import pydicom
import pytest

from ai_module import batch
from ai_module.batch import ArchiveLimitError, extract_zip, load_engine, process_file
from ai_module.dicom_io import PHI_TAGS, REQUIRED_TAGS
from tests.test_batch import OK_SPINE, SAMPLES
from tests.test_review_fixes import _upload, _zip, api  # noqa: F401

ORGANIZER_TEST = Path(__file__).resolve().parents[2] / "_extract" / "Для теста" / "Для теста"


# ---------------------------------------------------------------- лимиты

def test_zip_bomb_is_stopped_by_real_unpacked_size(tmp_path, monkeypatch):
    """5 МБ нулей сжимаются в несколько КБ: лимит считается по реально
    записанным байтам, а не по размеру архива."""
    monkeypatch.setattr(batch, "MAX_UNPACKED_BYTES", 2**20)
    bomb = tmp_path / "bomb.zip"
    with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("zeros.dcm", b"\0" * (5 * 2**20))
    assert bomb.stat().st_size < 64 * 1024
    with pytest.raises(ArchiveLimitError):
        extract_zip(bomb, tmp_path / "out")


def test_limit_is_shared_by_nested_archives(tmp_path, monkeypatch):
    monkeypatch.setattr(batch, "MAX_UNPACKED_BYTES", 2**20)
    inner = _zip({"part.bin": b"\1" * (700 * 1024)})
    outer = tmp_path / "outer.zip"
    outer.write_bytes(_zip({"a.zip": inner, "b.zip": inner}))
    with pytest.raises(ArchiveLimitError):
        extract_zip(outer, tmp_path / "out")


def test_too_many_files_in_archive(tmp_path, monkeypatch):
    monkeypatch.setattr(batch, "MAX_ARCHIVE_FILES", 3)
    arc = tmp_path / "many.zip"
    arc.write_bytes(_zip({f"{i}.dcm": b"x" for i in range(5)}))
    with pytest.raises(ArchiveLimitError):
        extract_zip(arc, tmp_path / "out")


def test_api_rejects_oversized_upload_and_archive(api, monkeypatch):  # noqa: F811
    import app.storage
    monkeypatch.setattr(app.storage, "MAX_UPLOAD_BYTES", 1024)
    r = _upload(api, "big.dcm", OK_SPINE.read_bytes())
    assert r.status_code == 413
    assert api.get("/studies").json() == []

    monkeypatch.setattr(app.storage, "MAX_UPLOAD_BYTES", 2**30)
    monkeypatch.setattr(batch, "MAX_UNPACKED_BYTES", 1024)
    arc = _zip({"01.dcm": OK_SPINE.read_bytes()})
    assert _upload(api, "a.zip", arc, "application/zip").status_code == 413
    assert api.get("/studies").json() == []
    r = api.post("/batch", files={"file": ("a.zip", arc, "application/zip")})
    assert r.status_code == 413


# ---------------------------------------------------------------- метаданные

# Всё, что нужно для чтения пикселей и геометрии, обязательные теги (их
# отсутствие — само по себе нарушение по ТЗ) и теги персональных данных (их
# наличие — тоже нарушение). Остальное ИИ использовать не должен.
_KEEP = set(REQUIRED_TAGS) | set(PHI_TAGS) | {
    "SamplesPerPixel", "BitsStored", "HighBit", "PixelRepresentation", "RescaleSlope",
    "RescaleIntercept", "PixelSpacing", "ImagerPixelSpacing", "WindowCenter", "WindowWidth",
    "PixelData", "SeriesInstanceUID", "SOPClassUID", "PatientID",
}
_MISLEADING = {
    "Manufacturer": "OTHER VENDOR", "ManufacturerModelName": "Model X", "StationName": "ST-99",
    "SeriesDescription": "HIP LEFT", "StudyDescription": "SPINE L1-L4", "ProtocolName": "HIP",
    "BodyPartExamined": "HIP", "ImageComments": "bad positioning", "DeviceSerialNumber": "0",
}


def _result(path, engine):
    row = process_file(path, engine)
    return (row["anatomical_region"], row["quality_class"], row["violation_type"],
            row["processing_status"], round(float(row["quality_prob"] or 0), 4))


def _variants(src: Path, tmp_path: Path) -> list[Path]:
    stripped = pydicom.dcmread(src)
    for elem in list(stripped):
        if elem.keyword and elem.keyword not in _KEEP:
            del stripped[elem.tag]
    misleading = pydicom.dcmread(src)
    for kw, value in _MISLEADING.items():
        setattr(misleading, kw, value)
    out = []
    for name, ds in (("stripped", stripped), ("misleading", misleading)):
        p = tmp_path / f"{src.stem}_{name}.dcm"
        ds.save_as(p)
        out.append(p)
    return out


def _check_independent(files, tmp_path):
    engine = load_engine("hybrid")
    for src in files:
        expected = _result(src, engine)
        for variant in _variants(src, tmp_path):
            assert _result(variant, engine) == expected, variant.name


def test_ai_ignores_descriptive_metadata(tmp_path):
    """Ревью: признаки не должны браться из Manufacturer, StationName,
    SeriesDescription, ProtocolName и т.п. Стираем все такие теги или пишем в
    них заведомо ложное — результат ИИ тот же."""
    _check_independent(sorted(SAMPLES.glob("*.dcm")), tmp_path)


@pytest.mark.skipif(not ORGANIZER_TEST.is_dir(), reason="тестовые снимки организатора не в репозитории")
def test_ai_ignores_metadata_on_organizer_images(tmp_path):
    _check_independent(sorted(ORGANIZER_TEST.glob("*.dcm")), tmp_path)
