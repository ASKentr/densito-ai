from __future__ import annotations

import os
import shutil
import zipfile
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from fastapi.responses import FileResponse, Response
from sqlmodel import Session, select, func

from app.audit import log_action
from app.database import get_session
from app.dicom_utils import (
    check_anonymization, check_required_tags, extract_metadata, is_dicom_file,
    read_dicom, render_preview_png,
)
from app.models import (
    AnalysisResult, Finding, FindingSource, FindingStatus, OverallVerdict, ReviewStatus,
    Study, StudyImage, StudyStatus, User, ViolationType,
)
from app.schemas import FindingOut, ImageOut, StudyDetail, StudyListItem
from app.security import get_current_user
from app.storage import make_display_id, new_study_storage_dir

router = APIRouter(prefix="/studies", tags=["studies"])


def _finding_to_out(f: Finding, vt: ViolationType, reviewer_name: str | None) -> FindingOut:
    return FindingOut(
        id=f.id, image_id=f.image_id, violation_type_id=f.violation_type_id,
        violation_code=vt.code, violation_name=vt.name_ru, source=f.source.value,
        status=f.status, severity=f.severity, confidence=f.confidence,
        bbox_x=f.bbox_x, bbox_y=f.bbox_y, bbox_w=f.bbox_w, bbox_h=f.bbox_h,
        comment=f.comment, reviewed_by=reviewer_name,
    )


def _study_to_list_item(study: Study, analysis: AnalysisResult | None, findings_count: int) -> StudyListItem:
    return StudyListItem(
        id=study.id, display_id=study.display_id, study_date=study.study_date,
        body_part=study.body_part, study_description=study.study_description,
        modality=study.modality, status=study.status, review_status=study.review_status,
        overall_verdict=analysis.overall_verdict if analysis else None,
        overall_score=analysis.overall_score if analysis else None,
        findings_count=findings_count,
        anonymization_ok=study.anonymization_ok, created_at=study.created_at,
    )


@router.post("/upload", response_model=StudyDetail)
def upload_study(
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    rel_dir, abs_dir = new_study_storage_dir()
    upload_path = os.path.join(abs_dir, file.filename or "upload.bin")
    with open(upload_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    dicom_paths: list[str] = []
    source_kind = "dicom"
    if (file.filename or "").lower().endswith(".zip") or zipfile.is_zipfile(upload_path):
        source_kind = "zip"
        extract_dir = os.path.join(abs_dir, "extracted")
        os.makedirs(extract_dir, exist_ok=True)
        with zipfile.ZipFile(upload_path) as zf:
            zf.extractall(extract_dir)
        for root, _, files in os.walk(extract_dir):
            for name in files:
                p = os.path.join(root, name)
                if is_dicom_file(p):
                    dicom_paths.append(p)
        os.remove(upload_path)
    else:
        if not is_dicom_file(upload_path):
            shutil.rmtree(abs_dir, ignore_errors=True)
            raise HTTPException(400, "Файл не распознан как DICOM или ZIP-архив с DICOM-файлами")
        dicom_paths.append(upload_path)

    if not dicom_paths:
        shutil.rmtree(abs_dir, ignore_errors=True)
        raise HTTPException(400, "В загруженных данных не найдено ни одного DICOM-файла")

    seq = (session.exec(select(func.count()).select_from(Study)).one() or 0) + 1
    display_id = make_display_id(seq)
    while session.exec(select(Study).where(Study.display_id == display_id)).first():
        seq += 1
        display_id = make_display_id(seq)

    first_ds = read_dicom(dicom_paths[0])
    meta = extract_metadata(first_ds)

    study = Study(
        display_id=display_id,
        study_date=meta["study_date"],
        body_part=meta["body_part"],
        study_description=meta["study_description"],
        modality=meta["modality"],
        original_filename=file.filename or "",
        source_kind=source_kind,
        status=StudyStatus.uploaded,
        uploaded_by_id=user.id,
    )
    session.add(study)
    session.commit()
    session.refresh(study)

    phi_all_ok = True
    phi_report = []
    for p in dicom_paths:
        ds = read_dicom(p)
        anon = check_anonymization(ds)
        if not anon["ok"]:
            phi_all_ok = False
            phi_report.append({"file": os.path.basename(p), "found_phi": anon["found_phi"]})
        img_meta = extract_metadata(ds)
        image = StudyImage(
            study_id=study.id, filename=os.path.basename(p), storage_path=p,
            sop_instance_uid=img_meta["sop_instance_uid"],
            rows=img_meta["rows"], columns=img_meta["columns"],
        )
        session.add(image)

    study.anonymization_ok = phi_all_ok
    study.anonymization_report = {"issues": phi_report}
    study.updated_at = datetime.utcnow()
    session.add(study)
    session.commit()
    session.refresh(study)

    log_action(session, user, "upload_study", entity="study", entity_id=study.id,
               details=f"{len(dicom_paths)} файл(ов), source={source_kind}")

    return get_study(study.id, session, user)


@router.get("", response_model=list[StudyListItem])
def list_studies(
    search: str | None = None,
    status_filter: StudyStatus | None = Query(None, alias="status"),
    review_status: ReviewStatus | None = None,
    body_part: str | None = None,
    verdict: OverallVerdict | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    query = select(Study)
    if status_filter:
        query = query.where(Study.status == status_filter)
    if review_status:
        query = query.where(Study.review_status == review_status)
    if body_part:
        query = query.where(Study.body_part == body_part)
    if date_from:
        query = query.where(Study.study_date >= date_from)
    if date_to:
        query = query.where(Study.study_date <= date_to)

    studies = session.exec(query.order_by(Study.created_at.desc())).all()

    results = []
    for s in studies:
        if search:
            haystack = f"{s.display_id} {s.study_description or ''} {s.body_part or ''}".lower()
            if search.lower() not in haystack:
                continue
        analysis = s.analysis
        if verdict and (not analysis or analysis.overall_verdict != verdict):
            continue
        findings_count = session.exec(
            select(func.count()).select_from(Finding).where(Finding.study_id == s.id)
        ).one()
        results.append(_study_to_list_item(s, analysis, findings_count))
    return results


@router.get("/{study_id}", response_model=StudyDetail)
def get_study(study_id: int, session: Session = Depends(get_session), user: User = Depends(get_current_user)):
    study = session.get(Study, study_id)
    if not study:
        raise HTTPException(404, "Исследование не найдено")

    images = session.exec(select(StudyImage).where(StudyImage.study_id == study_id)).all()
    findings = session.exec(select(Finding).where(Finding.study_id == study_id)).all()
    vtypes = {vt.id: vt for vt in session.exec(select(ViolationType)).all()}
    users = {u.id: u for u in session.exec(select(User)).all()}

    findings_out = [
        _finding_to_out(f, vtypes[f.violation_type_id],
                         users[f.reviewed_by_id].username if f.reviewed_by_id else None)
        for f in findings
    ]
    analysis = study.analysis

    return StudyDetail(
        **_study_to_list_item(study, analysis, len(findings)).model_dump(),
        original_filename=study.original_filename,
        patient_pseudo_id=study.patient_pseudo_id,
        anonymization_report=study.anonymization_report,
        images=[ImageOut(id=i.id, filename=i.filename, rows=i.rows, columns=i.columns,
                          frame_index=i.frame_index) for i in images],
        findings=findings_out,
        model_version=analysis.model_version if analysis else None,
    )


@router.get("/{study_id}/images/{image_id}/preview")
def get_image_preview(study_id: int, image_id: int, session: Session = Depends(get_session),
                       user: User = Depends(get_current_user)):
    image = session.get(StudyImage, image_id)
    if not image or image.study_id != study_id:
        raise HTTPException(404, "Изображение не найдено")
    ds = read_dicom(image.storage_path)
    png_bytes = render_preview_png(ds)
    return Response(content=png_bytes, media_type="image/png")


@router.get("/{study_id}/images/{image_id}/raw")
def get_image_raw(study_id: int, image_id: int, session: Session = Depends(get_session),
                   user: User = Depends(get_current_user)):
    image = session.get(StudyImage, image_id)
    if not image or image.study_id != study_id:
        raise HTTPException(404, "Изображение не найдено")
    return FileResponse(image.storage_path, media_type="application/dicom",
                         filename=image.filename)


@router.post("/{study_id}/analyze", response_model=StudyDetail)
def analyze_study_endpoint(study_id: int, session: Session = Depends(get_session),
                            user: User = Depends(get_current_user)):
    from ai_module.heuristics import analyze_study as run_ai

    study = session.get(Study, study_id)
    if not study:
        raise HTTPException(404, "Исследование не найдено")

    images = session.exec(
        select(StudyImage).where(StudyImage.study_id == study_id).order_by(StudyImage.id)
    ).all()
    if not images:
        raise HTTPException(400, "В исследовании нет изображений для анализа")

    study.status = StudyStatus.processing
    session.add(study)
    session.commit()

    # удаляем предыдущие AI-находки при повторном запуске анализа (экспертные остаются)
    old_ai_findings = session.exec(
        select(Finding).where(Finding.study_id == study_id, Finding.source == FindingSource.ai)
    ).all()
    for f in old_ai_findings:
        session.delete(f)
    old_analysis = session.exec(select(AnalysisResult).where(AnalysisResult.study_id == study_id)).first()
    if old_analysis:
        session.delete(old_analysis)
    session.commit()

    try:
        result = run_ai([img.storage_path for img in images])
    except Exception as exc:
        study.status = StudyStatus.error
        session.add(study)
        session.commit()
        raise HTTPException(500, f"Ошибка AI-анализа: {exc}")

    vtype_by_code = {vt.code: vt for vt in session.exec(select(ViolationType)).all()}
    for f in result.findings:
        vt = vtype_by_code.get(f.violation_code)
        if not vt:
            continue  # неизвестный код нарушения (справочник изменён администратором) — пропускаем
        image = images[f.image_index] if f.image_index < len(images) else images[0]
        bbox = f.bbox or (None, None, None, None)
        finding = Finding(
            study_id=study_id, image_id=image.id, violation_type_id=vt.id,
            source=FindingSource.ai, status=FindingStatus.pending,
            severity=f.severity, confidence=f.confidence,
            bbox_x=bbox[0], bbox_y=bbox[1], bbox_w=bbox[2], bbox_h=bbox[3],
            comment=f.explanation,
        )
        session.add(finding)

    analysis = AnalysisResult(
        study_id=study_id, overall_verdict=OverallVerdict(result.overall_verdict),
        overall_score=result.overall_score, model_version=result.model_version,
        findings_count=len(result.findings),
        raw_json={"notes": result.notes},
    )
    session.add(analysis)

    study.status = StudyStatus.analyzed
    study.updated_at = datetime.utcnow()
    session.add(study)
    session.commit()

    log_action(session, user, "run_analysis", entity="study", entity_id=study_id,
               details=f"verdict={result.overall_verdict}, score={result.overall_score}, "
                       f"findings={len(result.findings)}")

    return get_study(study_id, session, user)
