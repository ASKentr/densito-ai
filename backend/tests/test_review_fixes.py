"""Регрессионные тесты на дефекты из внешнего ревью 2026-09-29 (review-densito-ai.md).
Каждый тест воспроизводит сценарий ревьюера и проверяет исправленное поведение."""
import io
import zipfile

import numpy as np
import pydicom
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from tests.test_batch import OK_SPINE, SAMPLES

MISALIGNED = SAMPLES / "06_lspine_misaligned.dcm"


@pytest.fixture
def api(tmp_path, monkeypatch):
    """Приложение с временной БД, справочником нарушений и пользователем-экспертом."""
    from ai_module.violation_catalog import DEFAULT_VIOLATION_TYPES
    from app.config import settings
    from app.database import get_session
    from app.main import app
    from app.models import Role, User, ViolationType
    from app.security import get_current_user

    monkeypatch.setattr(settings, "storage_dir", str(tmp_path))
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        for item in DEFAULT_VIOLATION_TYPES:
            s.add(ViolationType(**item))
        user = User(username="expert", full_name="Эксперт", role=Role.admin, password_hash="x")
        s.add(user)
        s.commit()
        s.refresh(user)

    def _session():
        with Session(engine) as s:
            yield s

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_current_user] = lambda: user
    yield TestClient(app)
    app.dependency_overrides.clear()


def _upload(api, name, data, mime="application/dicom"):
    return api.post("/studies/upload", files={"file": (name, data, mime)})


def _zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n, b in entries.items():
            z.writestr(n, b)
    return buf.getvalue()


# ---------------------------------------------------------------- замечание 1

def test_reanalysis_keeps_expert_decisions_and_requires_new_review(api):
    sid = _upload(api, "06.dcm", MISALIGNED.read_bytes()).json()["id"]
    ai = [f for f in api.post(f"/studies/{sid}/analyze").json()["findings"] if f["source"] == "ai"]
    assert ai, "на снимке с наклоном оси ИИ должен что-то найти"
    for f in ai:
        assert api.patch(f"/findings/{f['id']}", json={"action": "reject"}).status_code == 200
    assert api.post(f"/studies/{sid}/complete_review").status_code == 200
    assert api.get(f"/studies/{sid}").json()["review_status"] == "reviewed"

    after = api.post(f"/studies/{sid}/analyze").json()
    assert after["review_status"] == "not_reviewed"                       # новая версия — новая проверка
    assert all(f["status"] == "pending" for f in after["findings"] if f["source"] == "ai")

    history = api.get(f"/studies/{sid}/history").json()
    assert len(history) == 1 and history[0]["review_status"] == "reviewed"
    archived = [f for f in history[0]["findings"] if f["source"] == "ai"]
    assert len(archived) == len(ai)
    assert all(f["status"] == "rejected" and f["reviewed_by"] == "expert" for f in archived)


def test_failed_reanalysis_loses_nothing(api, monkeypatch):
    from ai_module import hybrid
    sid = _upload(api, "06.dcm", MISALIGNED.read_bytes()).json()["id"]
    before = api.post(f"/studies/{sid}/analyze").json()

    def boom(paths):
        raise RuntimeError("модель недоступна")
    monkeypatch.setattr(hybrid, "analyze_study", boom)
    assert api.post(f"/studies/{sid}/analyze").status_code == 500

    now = api.get(f"/studies/{sid}").json()
    assert now["status"] == "analyzed"
    assert [f["id"] for f in now["findings"]] == [f["id"] for f in before["findings"]]
    assert api.get(f"/studies/{sid}/history").json() == []


def test_complete_review_checked_by_server(api):
    sid = _upload(api, "06.dcm", MISALIGNED.read_bytes()).json()["id"]
    r = api.post(f"/studies/{sid}/complete_review")
    assert r.status_code == 409 and "не проанализировано" in r.json()["detail"]
    api.post(f"/studies/{sid}/analyze")
    r = api.post(f"/studies/{sid}/complete_review")                     # находки ещё не проверены
    assert r.status_code == 409 and "непроверенные" in r.json()["detail"]


# ---------------------------------------------------------------- замечание 2

def test_zip_with_different_studies_creates_separate_cards(api):
    bundle = (SAMPLES / "studies_bundle.zip").read_bytes()
    uids, no_uid = set(), 0
    with zipfile.ZipFile(io.BytesIO(bundle)) as z:
        for n in z.namelist():
            uid = getattr(pydicom.dcmread(io.BytesIO(z.read(n)), stop_before_pixels=True), "StudyInstanceUID", "")
            if uid:
                uids.add(uid)
            else:
                no_uid += 1                                  # 08_lspine_missing_tags — без UID
    r = _upload(api, "bundle.zip", bundle, "application/zip")
    assert r.status_code == 200
    created = r.json()["created_study_ids"]
    assert len(created) == len(uids) + no_uid == 9
    for sid in created:
        assert len(api.get(f"/studies/{sid}").json()["images"]) == 1


def test_zip_with_one_study_is_one_card(api):
    data = OK_SPINE.read_bytes()
    r = _upload(api, "one.zip", _zip({"a.dcm": data, "b/a_copy.dcm": data}), "application/zip")
    assert r.json()["created_study_ids"] == [r.json()["id"]] and len(r.json()["images"]) == 2


def test_files_without_study_uid_are_not_merged(api):
    def no_uid(src):
        ds = pydicom.dcmread(src)
        del ds.StudyInstanceUID
        buf = io.BytesIO()
        ds.save_as(buf)
        return buf.getvalue()
    r = _upload(api, "nouid.zip", _zip({"x.dcm": no_uid(OK_SPINE), "y.dcm": no_uid(MISALIGNED)}), "application/zip")
    assert len(r.json()["created_study_ids"]) == 2


# ---------------------------------------------------------------- замечание 4

def test_used_category_cannot_be_deleted(api):
    vt = api.post("/admin/violation-types", json={"code": "custom_x", "name_ru": "Своя", "category": "other"}).json()
    sid = _upload(api, "06.dcm", MISALIGNED.read_bytes()).json()["id"]
    image_id = api.get(f"/studies/{sid}").json()["images"][0]["id"]
    assert api.post(f"/studies/{sid}/findings", json={
        "image_id": image_id, "violation_type_id": vt["id"], "severity": "low", "comment": "",
        "bbox_x": 0.1, "bbox_y": 0.1, "bbox_w": 0.2, "bbox_h": 0.2}).status_code == 200
    assert api.delete(f"/admin/violation-types/{vt['id']}").status_code == 409
    assert api.get(f"/studies/{sid}").status_code == 200


# ---------------------------------------------------------------- замечание 7

def test_monochrome1_gives_same_result_as_monochrome2(tmp_path):
    from ai_module import heuristics
    ds = pydicom.dcmread(OK_SPINE)
    bits = int(ds.BitsStored)
    arr = ds.pixel_array.astype(np.int64)
    ds.PixelData = ((2 ** bits - 1) - arr).astype(ds.pixel_array.dtype).tobytes()
    ds.PhotometricInterpretation = "MONOCHROME1"
    if "WindowCenter" in ds:
        ds.WindowCenter = (2 ** bits - 1) - float(ds.WindowCenter if not isinstance(ds.WindowCenter, pydicom.multival.MultiValue) else ds.WindowCenter[0])
    inv = tmp_path / "mono1.dcm"
    ds.save_as(inv)

    r0, f0 = heuristics.analyze_image(0, str(OK_SPINE))
    r1, f1 = heuristics.analyze_image(0, str(inv))
    assert r1.anatomical_region == r0.anatomical_region
    assert sorted(f.violation_code for f in f1) == sorted(f.violation_code for f in f0)


# ---------------------------------------------------------------- замечание 8

def test_pr_auc_does_not_depend_on_order_of_ties():
    from ai_module.eval_pipeline_cv import _pr_auc
    assert _pr_auc([1, 0], [0.5, 0.5]) == _pr_auc([0, 1], [0.5, 0.5]) == 0.5
    assert abs(_pr_auc([1, 0, 1, 0], [0.9, 0.8, 0.7, 0.1]) - (1 + 2 / 3) / 2) < 1e-12


# ---------------------------------------------------------------- замечание 9

def test_empty_file_is_rejected(api, tmp_path):
    r = _upload(api, "empty.txt", b"", "text/plain")
    assert r.status_code == 400
    assert api.get("/studies").json() == []
    r = _upload(api, "mixed.zip", _zip({"empty.dcm": b"", "notes.txt": b"x", "ok.dcm": OK_SPINE.read_bytes()}),
                "application/zip")
    assert r.status_code == 200 and len(r.json()["images"]) == 1
