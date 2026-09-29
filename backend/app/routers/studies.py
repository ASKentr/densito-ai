from __future__ import annotations

import os
import shutil
import zipfile

import pydicom
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from fastapi.responses import FileResponse, Response
from sqlmodel import Session, select, func

from ai_module.batch import ArchiveLimitError, extract_zip
from app.audit import log_action
from app.database import get_session
from app.dicom_utils import (
    check_anonymization, check_required_tags, extract_metadata, is_dicom_image,
    read_dicom, render_preview_png,
)
from app.models import (
    AnalysisArchive, AnalysisResult, Finding, FindingSource, FindingStatus, OverallVerdict, ReviewStatus,
    Study, StudyImage, StudyStatus, User, ViolationType,
)
from app.schemas import AnalysisArchiveOut, FindingOut, ImageOut, StudyDetail, StudyListItem
from app.security import get_current_user
from app.storage import UploadTooLarge, make_display_id, new_study_storage_dir, safe_upload_name, save_upload

router = APIRouter(prefix="/studies", tags=["studies"])


def _pixel_spacing(path: str) -> list[float] | None:
    from ai_module.calibration import get_pixel_spacing_mm
    try:
        return list(get_pixel_spacing_mm(pydicom.dcmread(path, stop_before_pixels=True, force=True)))
    except Exception:
        return None


# на случай записи без категории в справочнике (старые БД без внешних ключей):
# карточка открывается, а не падает с 500
_MISSING_VT = ViolationType(id=0, code="deleted", name_ru="Удалённая категория", category="")


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
    upload_name = safe_upload_name(file.filename)
    upload_path = os.path.join(abs_dir, upload_name)
    try:
        save_upload(file.file, upload_path)
    except UploadTooLarge as exc:
        shutil.rmtree(abs_dir, ignore_errors=True)
        raise HTTPException(413, f"Слишком большой файл: {exc}")

    dicom_paths: list[str] = []
    skipped = 0
    source_kind = "dicom"
    if upload_name.lower().endswith(".zip") or zipfile.is_zipfile(upload_path):
        source_kind = "zip"
        extract_dir = Path(abs_dir) / "extracted"
        try:
            # та же распаковка, что в пакетной обработке: защита от выхода из папки,
            # вложенные архивы, имена в cp866
            extract_zip(Path(upload_path), extract_dir)
        except zipfile.BadZipFile:
            shutil.rmtree(abs_dir, ignore_errors=True)
            raise HTTPException(400, "Повреждённый ZIP-архив")
        except ArchiveLimitError as exc:
            shutil.rmtree(abs_dir, ignore_errors=True)
            raise HTTPException(413, f"Архив превышает лимит: {exc}")
        os.remove(upload_path)
        for p in sorted(extract_dir.rglob("*")):
            if p.is_file() and not p.name.startswith("._") and "__MACOSX" not in p.parts:
                if is_dicom_image(str(p)):
                    dicom_paths.append(str(p))
                else:
                    skipped += 1
    else:
        if not is_dicom_image(upload_path):
            shutil.rmtree(abs_dir, ignore_errors=True)
            raise HTTPException(400, "Файл не распознан как DICOM-изображение (нет размеров или "
                                     "пиксельных данных) или ZIP-архив с такими файлами")
        dicom_paths.append(upload_path)

    if not dicom_paths:
        shutil.rmtree(abs_dir, ignore_errors=True)
        raise HTTPException(400, "В загруженных данных не найдено ни одного DICOM-изображения")

    # Одна карточка = одно исследование (StudyInstanceUID). Архив с разными
    # исследованиями раскладывается по отдельным карточкам; файл без UID — своя
    # карточка, объединять только по принадлежности к архиву нельзя
    # (внешнее ревью, замечание 2).
    groups: dict[str, list[str]] = {}
    for p in dicom_paths:
        uid = str(getattr(read_dicom(p), "StudyInstanceUID", "") or "").strip()
        groups.setdefault(uid or f"__no_uid__{p}", []).append(p)

    created = [_create_study(session, user, paths, file.filename or "", source_kind) for paths in groups.values()]
    for study in created:
        log_action(session, user, "upload_study", entity="study", entity_id=study.id,
                   details=f"{len(study.images)} файл(ов), source={source_kind}"
                           + (f", архив разделён на {len(created)} исслед." if len(created) > 1 else "")
                           + (f", пропущено не-DICOM: {skipped}" if skipped else ""))

    detail = get_study(created[0].id, session, user)
    detail.created_study_ids = [s.id for s in created]
    return detail


def _create_study(session: Session, user: User, dicom_paths: list[str], original_filename: str,
                  source_kind: str) -> Study:
    seq = (session.exec(select(func.count()).select_from(Study)).one() or 0) + 1
    display_id = make_display_id(seq)
    while session.exec(select(Study).where(Study.display_id == display_id)).first():
        seq += 1
        display_id = make_display_id(seq)

    meta = extract_metadata(read_dicom(dicom_paths[0]))
    study = Study(
        display_id=display_id,
        study_date=meta["study_date"],
        body_part=meta["body_part"],
        study_description=meta["study_description"],
        modality=meta["modality"],
        original_filename=original_filename,
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
        session.add(StudyImage(
            study_id=study.id, filename=os.path.basename(p), storage_path=p,
            sop_instance_uid=img_meta["sop_instance_uid"],
            rows=img_meta["rows"], columns=img_meta["columns"],
        ))

    study.anonymization_ok = phi_all_ok
    study.anonymization_report = {"issues": phi_report}
    study.updated_at = datetime.utcnow()
    session.add(study)
    session.commit()
    session.refresh(study)
    return study


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
        _finding_to_out(f, vtypes.get(f.violation_type_id) or _MISSING_VT,
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
                          frame_index=i.frame_index, anatomical_region=i.anatomical_region,
                          quality_prob=i.quality_prob, pixel_spacing_mm=_pixel_spacing(i.storage_path))
                for i in images],
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


_FINDING_STATUS_RU = {
    FindingStatus.pending: "не проверено экспертом",
    FindingStatus.confirmed: "подтверждено экспертом",
    FindingStatus.modified: "изменено экспертом",
    FindingStatus.added: "добавлено экспертом",
}
_REVIEW_STATUS_RU = {
    ReviewStatus.not_reviewed: "Экспертная проверка не проводилась",
    ReviewStatus.in_review: "На экспертной проверке",
    ReviewStatus.reviewed: "Проверено экспертом",
}


@router.get("/{study_id}/images/{image_id}/sr",
            summary="Текстовый отчёт о нарушениях в формате DICOM SR (ТЗ п.2.6)")
def get_image_sr(study_id: int, image_id: int, session: Session = Depends(get_session),
                 user: User = Depends(get_current_user)):
    """Basic Text SR по текущему состоянию карточки: находки ИИ (кроме отклонённых
    экспертом) и находки, добавленные экспертом, со статусом проверки."""
    from ai_module.dicom_sr import SRContent, SRFinding, build_sr, sr_bytes

    image = session.get(StudyImage, image_id)
    if not image or image.study_id != study_id:
        raise HTTPException(404, "Изображение не найдено")
    study = session.get(Study, study_id)
    if not study.analysis:
        raise HTTPException(409, "Исследование ещё не проанализировано")

    vtypes = {vt.id: vt for vt in session.exec(select(ViolationType)).all()}
    findings = session.exec(select(Finding).where(
        Finding.image_id == image_id, Finding.status != FindingStatus.rejected).order_by(Finding.id)).all()
    sr_findings = []
    for f in findings:
        detail = f.comment.strip()
        status = _FINDING_STATUS_RU.get(f.status, "")
        sr_findings.append(SRFinding((vtypes.get(f.violation_type_id) or _MISSING_VT).name_ru,
                                     f"{detail} ({status})" if detail else f"({status})"))

    ds = read_dicom(image.storage_path)
    content = SRContent(
        region=image.anatomical_region or "", quality_class=1 if sr_findings else 0,
        quality_prob=image.quality_prob, model_version=study.analysis.model_version,
        findings=sr_findings, review_status=_REVIEW_STATUS_RU.get(study.review_status, ""),
    )
    log_action(session, user, "export_sr", entity="study", entity_id=study_id, details=f"image={image_id}")
    # имя файла — только ASCII (заголовки HTTP в latin-1; исходное имя может быть кириллическим)
    return Response(content=sr_bytes(build_sr(ds, content)), media_type="application/dicom",
                    headers={"Content-Disposition": f'attachment; filename="{study.display_id}_image{image_id}.sr.dcm"'})


def _archive_snapshot(session: Session, study: Study, analysis: AnalysisResult | None,
                      images: list[StudyImage], user: User) -> AnalysisArchive:
    """Снимок текущего результата ИИ и всех находок (с решениями эксперта)."""
    vtypes = {vt.id: vt for vt in session.exec(select(ViolationType)).all()}
    users = {u.id: u.username for u in session.exec(select(User)).all()}
    files = {i.id: i.filename for i in images}
    findings = session.exec(select(Finding).where(Finding.study_id == study.id).order_by(Finding.id)).all()
    return AnalysisArchive(
        study_id=study.id,
        model_version=analysis.model_version if analysis else "",
        overall_verdict=analysis.overall_verdict.value if analysis else "",
        analyzed_at=analysis.created_at if analysis else None,
        review_status=study.review_status.value,
        archived_by_id=user.id,
        findings=[{
            "source": f.source.value, "status": f.status.value,
            "violation_code": vtypes[f.violation_type_id].code if f.violation_type_id in vtypes else "",
            "violation_name": vtypes[f.violation_type_id].name_ru if f.violation_type_id in vtypes else "",
            "severity": f.severity.value, "confidence": f.confidence, "comment": f.comment,
            "image": files.get(f.image_id, ""),
            "reviewed_by": users.get(f.reviewed_by_id), "reviewed_at": f.reviewed_at.isoformat() if f.reviewed_at else None,
        } for f in findings],
    )


@router.get("/{study_id}/history", response_model=list[AnalysisArchiveOut],
            summary="История анализов: прежние результаты ИИ и решения эксперта")
def get_study_history(study_id: int, session: Session = Depends(get_session),
                      user: User = Depends(get_current_user)):
    if not session.get(Study, study_id):
        raise HTTPException(404, "Исследование не найдено")
    users = {u.id: u.username for u in session.exec(select(User)).all()}
    rows = session.exec(select(AnalysisArchive).where(AnalysisArchive.study_id == study_id)
                        .order_by(AnalysisArchive.id.desc())).all()
    return [AnalysisArchiveOut(
        id=r.id, model_version=r.model_version, overall_verdict=r.overall_verdict,
        analyzed_at=r.analyzed_at, review_status=r.review_status, archived_at=r.archived_at,
        archived_by=users.get(r.archived_by_id), findings=r.findings,
    ) for r in rows]


@router.post("/{study_id}/analyze", response_model=StudyDetail)
def analyze_study_endpoint(study_id: int, session: Session = Depends(get_session),
                            user: User = Depends(get_current_user)):
    # Гибрид: эвристики + CNN для укладки бедра (docs/ГИБРИД_МОДЕЛЬ.md).
    # Откат на чистые эвристики — импорт из ai_module.heuristics.
    from ai_module.hybrid import analyze_study as run_ai

    study = session.get(Study, study_id)
    if not study:
        raise HTTPException(404, "Исследование не найдено")

    images = session.exec(
        select(StudyImage).where(StudyImage.study_id == study_id).order_by(StudyImage.id)
    ).all()
    if not images:
        raise HTTPException(400, "В исследовании нет изображений для анализа")

    prev_status = study.status
    study.status = StudyStatus.processing
    session.add(study)
    session.commit()

    # Сначала — анализ. Прежний результат и решения эксперта не трогаем, пока новый
    # не получен (внешнее ревью, замечание 1): неудачный повтор ничего не теряет.
    try:
        result = run_ai([img.storage_path for img in images])
    except Exception as exc:
        study.status = StudyStatus.analyzed if prev_status == StudyStatus.analyzed else StudyStatus.error
        session.add(study)
        session.commit()
        raise HTTPException(500, f"Ошибка ИИ-анализа: {exc}")

    # Прежний результат ИИ вместе с решениями эксперта — в архив (история проверок),
    # затем замена одной транзакцией.
    old_ai_findings = session.exec(
        select(Finding).where(Finding.study_id == study_id, Finding.source == FindingSource.ai)
    ).all()
    old_analysis = session.exec(select(AnalysisResult).where(AnalysisResult.study_id == study_id)).first()
    if old_analysis or old_ai_findings:
        session.add(_archive_snapshot(session, study, old_analysis, images, user))
    for f in old_ai_findings:
        session.delete(f)
    if old_analysis:
        session.delete(old_analysis)
    session.flush()

    # anatomical_region / quality_prob — на карточку изображения (шаг 6 плана
    # переделки): та же логика, что в batch_predict.py (ai_module/quality_prob.py),
    # чтобы веб-интерфейс и выгружаемая таблица не расходились в цифрах.
    from ai_module.quality_prob import compute_quality_prob
    from ai_module.submission_mapping import map_anatomical_region

    for idx, image in enumerate(images):
        report = result.image_reports[idx] if idx < len(result.image_reports) else None
        if report is None:
            continue
        image_findings = [f for f in result.findings if f.image_index == idx]
        image.anatomical_region = map_anatomical_region(report.anatomical_region)
        image.quality_prob = round(compute_quality_prob(report.anatomical_region, report.metrics, image_findings), 4)
        session.add(image)

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
    # новый результат ИИ требует новой проверки; находки эксперта сохраняются
    has_expert = session.exec(select(Finding.id).where(
        Finding.study_id == study_id, Finding.source == FindingSource.expert)).first() is not None
    study.review_status = ReviewStatus.in_review if has_expert else ReviewStatus.not_reviewed
    study.updated_at = datetime.utcnow()
    session.add(study)
    session.commit()

    log_action(session, user, "run_analysis", entity="study", entity_id=study_id,
               details=f"verdict={result.overall_verdict}, score={result.overall_score}, "
                       f"findings={len(result.findings)}")

    return get_study(study_id, session, user)
