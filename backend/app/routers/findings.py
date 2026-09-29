from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from app.audit import log_action
from app.database import get_session
from app.models import (
    Finding, FindingSource, FindingStatus, ReviewStatus, Study, StudyImage, StudyStatus, User, ViolationType,
)
from app.schemas import FindingCreate, FindingOut, FindingReviewAction
from app.security import get_current_user

router = APIRouter(tags=["findings"])


def _out(f: Finding, session: Session) -> FindingOut:
    vt = session.get(ViolationType, f.violation_type_id)
    reviewer = session.get(User, f.reviewed_by_id) if f.reviewed_by_id else None
    return FindingOut(
        id=f.id, image_id=f.image_id, violation_type_id=f.violation_type_id,
        violation_code=vt.code, violation_name=vt.name_ru, source=f.source.value,
        status=f.status, severity=f.severity, confidence=f.confidence,
        bbox_x=f.bbox_x, bbox_y=f.bbox_y, bbox_w=f.bbox_w, bbox_h=f.bbox_h,
        comment=f.comment, reviewed_by=reviewer.username if reviewer else None,
    )


def _touch_review_status(session: Session, study_id: int):
    study = session.get(Study, study_id)
    if study and study.review_status == ReviewStatus.not_reviewed:
        study.review_status = ReviewStatus.in_review
        study.updated_at = datetime.utcnow()
        session.add(study)
        session.commit()


@router.patch("/findings/{finding_id}", response_model=FindingOut)
def review_finding(finding_id: int, action: FindingReviewAction,
                    session: Session = Depends(get_session), user: User = Depends(get_current_user)):
    finding = session.get(Finding, finding_id)
    if not finding:
        raise HTTPException(404, "Нарушение не найдено")

    if action.action == "confirm":
        finding.status = FindingStatus.confirmed
    elif action.action == "reject":
        finding.status = FindingStatus.rejected
    elif action.action == "modify":
        finding.status = FindingStatus.modified
        if action.violation_type_id:
            vt = session.get(ViolationType, action.violation_type_id)
            if not vt:
                raise HTTPException(400, "Указанный тип нарушения не найден")
            finding.violation_type_id = action.violation_type_id
        if action.severity:
            finding.severity = action.severity
    else:
        raise HTTPException(400, "Неизвестное действие: используйте confirm | reject | modify")

    if action.comment is not None:
        finding.comment = action.comment
    finding.reviewed_by_id = user.id
    finding.reviewed_at = datetime.utcnow()
    finding.updated_at = datetime.utcnow()
    session.add(finding)
    session.commit()
    session.refresh(finding)

    _touch_review_status(session, finding.study_id)
    log_action(session, user, f"finding_{action.action}", entity="finding", entity_id=finding.id,
               details=f"study_id={finding.study_id}")
    return _out(finding, session)


@router.post("/studies/{study_id}/findings", response_model=FindingOut)
def add_expert_finding(study_id: int, payload: FindingCreate,
                        session: Session = Depends(get_session), user: User = Depends(get_current_user)):
    study = session.get(Study, study_id)
    if not study:
        raise HTTPException(404, "Исследование не найдено")
    image = session.get(StudyImage, payload.image_id)
    if not image or image.study_id != study_id:
        raise HTTPException(400, "Изображение не относится к этому исследованию")
    vt = session.get(ViolationType, payload.violation_type_id)
    if not vt:
        raise HTTPException(400, "Указанный тип нарушения не найден")

    finding = Finding(
        study_id=study_id, image_id=payload.image_id, violation_type_id=payload.violation_type_id,
        source=FindingSource.expert, status=FindingStatus.added, severity=payload.severity,
        confidence=None, bbox_x=payload.bbox_x, bbox_y=payload.bbox_y,
        bbox_w=payload.bbox_w, bbox_h=payload.bbox_h, comment=payload.comment,
        reviewed_by_id=user.id, reviewed_at=datetime.utcnow(),
    )
    session.add(finding)
    session.commit()
    session.refresh(finding)

    _touch_review_status(session, study_id)
    log_action(session, user, "finding_added_by_expert", entity="finding", entity_id=finding.id,
               details=f"study_id={study_id}, code={vt.code}")
    return _out(finding, session)


@router.post("/studies/{study_id}/complete_review")
def complete_review(study_id: int, session: Session = Depends(get_session),
                     user: User = Depends(get_current_user)):
    study = session.get(Study, study_id)
    if not study:
        raise HTTPException(404, "Исследование не найдено")
    # условия завершения проверяет сервер, а не только интерфейс (внешнее ревью, замечание 1)
    if study.status != StudyStatus.analyzed or study.analysis is None:
        raise HTTPException(409, "Исследование ещё не проанализировано — завершать проверку нечего")
    pending = session.exec(select(Finding.id).where(
        Finding.study_id == study_id, Finding.source == FindingSource.ai,
        Finding.status == FindingStatus.pending)).all()
    if pending:
        raise HTTPException(409, f"Остались непроверенные находки ИИ: {len(pending)}. "
                                 "Подтвердите, отклоните или измените их перед завершением проверки")
    study.review_status = ReviewStatus.reviewed
    study.updated_at = datetime.utcnow()
    session.add(study)
    session.commit()
    log_action(session, user, "complete_review", entity="study", entity_id=study_id)
    return {"ok": True}
