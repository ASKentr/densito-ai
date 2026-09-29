from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select, func

from app.audit import log_action
from app.database import get_session
from app.models import AnalysisResult, AuditLogEntry, Finding, OverallVerdict, ReviewStatus, Study, User, ViolationType
from app.schemas import (
    AuditLogOut, StatsOut, UserCreate, UserOut, ViolationTypeCreate, ViolationTypeOut, ViolationTypeUpdate,
)
from app.security import get_current_user, hash_password, require_admin

# Внимание: этот роутер НЕ защищён целиком через require_admin на уровне
# APIRouter — справочник нарушений (GET) должен быть доступен и эксперту
# (нужен для экранов подтверждения/изменения категории находок), а не только
# администратору. Управление справочником (POST/PATCH/DELETE), пользователями,
# аудит-логом и статистикой — по-прежнему только для администратора, это
# обеспечивается явным Depends(require_admin) в каждом соответствующем эндпоинте.
router = APIRouter(prefix="/admin", tags=["admin"])


# --- справочник нарушений ---

@router.get("/violation-types", response_model=list[ViolationTypeOut])
def list_violation_types(session: Session = Depends(get_session), user: User = Depends(get_current_user)):
    return session.exec(select(ViolationType).order_by(ViolationType.category)).all()


@router.post("/violation-types", response_model=ViolationTypeOut)
def create_violation_type(payload: ViolationTypeCreate, session: Session = Depends(get_session),
                           user: User = Depends(require_admin)):
    if session.exec(select(ViolationType).where(ViolationType.code == payload.code)).first():
        raise HTTPException(400, "Нарушение с таким code уже существует")
    vt = ViolationType(**payload.model_dump(), is_system=False)
    session.add(vt)
    session.commit()
    session.refresh(vt)
    log_action(session, user, "create_violation_type", entity="violation_type", entity_id=vt.id)
    return vt


@router.patch("/violation-types/{vt_id}", response_model=ViolationTypeOut)
def update_violation_type(vt_id: int, payload: ViolationTypeUpdate, session: Session = Depends(get_session),
                           user: User = Depends(require_admin)):
    vt = session.get(ViolationType, vt_id)
    if not vt:
        raise HTTPException(404, "Тип нарушения не найден")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(vt, field, value)
    session.add(vt)
    session.commit()
    session.refresh(vt)
    log_action(session, user, "update_violation_type", entity="violation_type", entity_id=vt.id)
    return vt


@router.delete("/violation-types/{vt_id}")
def delete_violation_type(vt_id: int, session: Session = Depends(get_session), user: User = Depends(require_admin)):
    vt = session.get(ViolationType, vt_id)
    if not vt:
        raise HTTPException(404, "Тип нарушения не найден")
    if vt.is_system:
        raise HTTPException(400, "Системный тип нарушения нельзя удалить, только деактивировать "
                                  "(is_active=false) — иначе AI-модуль не сможет на него ссылаться")
    session.delete(vt)
    session.commit()
    log_action(session, user, "delete_violation_type", entity="violation_type", entity_id=vt_id)
    return {"ok": True}


# --- пользователи ---

@router.get("/users", response_model=list[UserOut])
def list_users(session: Session = Depends(get_session), user: User = Depends(require_admin)):
    return session.exec(select(User)).all()


@router.post("/users", response_model=UserOut)
def create_user(payload: UserCreate, session: Session = Depends(get_session), user: User = Depends(require_admin)):
    if session.exec(select(User).where(User.username == payload.username)).first():
        raise HTTPException(400, "Пользователь с таким именем уже существует")
    new_user = User(username=payload.username, full_name=payload.full_name, role=payload.role,
                     password_hash=hash_password(payload.password))
    session.add(new_user)
    session.commit()
    session.refresh(new_user)
    log_action(session, user, "create_user", entity="user", entity_id=new_user.id,
               details=f"role={payload.role}")
    return new_user


@router.patch("/users/{user_id}/deactivate", response_model=UserOut)
def deactivate_user(user_id: int, session: Session = Depends(get_session), user: User = Depends(require_admin)):
    target = session.get(User, user_id)
    if not target:
        raise HTTPException(404, "Пользователь не найден")
    target.is_active = False
    session.add(target)
    session.commit()
    session.refresh(target)
    log_action(session, user, "deactivate_user", entity="user", entity_id=user_id)
    return target


# --- аудит-лог ---

@router.get("/audit-log", response_model=list[AuditLogOut])
def audit_log(limit: int = 200, session: Session = Depends(get_session), user: User = Depends(require_admin)):
    entries = session.exec(select(AuditLogEntry).order_by(AuditLogEntry.created_at.desc()).limit(limit)).all()
    return entries


# --- статистика ---

@router.get("/stats", response_model=StatsOut)
def stats(session: Session = Depends(get_session), user: User = Depends(require_admin)):
    total = session.exec(select(func.count()).select_from(Study)).one()
    analyzed = session.exec(select(func.count()).select_from(Study).where(Study.status == "analyzed")).one()
    qualitative = session.exec(
        select(func.count()).select_from(AnalysisResult).where(AnalysisResult.overall_verdict == OverallVerdict.qualitative)
    ).one()
    non_qualitative = session.exec(
        select(func.count()).select_from(AnalysisResult).where(AnalysisResult.overall_verdict == OverallVerdict.non_qualitative)
    ).one()
    reviewed = session.exec(
        select(func.count()).select_from(Study).where(Study.review_status == ReviewStatus.reviewed)
    ).one()
    pending_review = session.exec(
        select(func.count()).select_from(Study).where(Study.review_status != ReviewStatus.reviewed)
    ).one()

    findings_by_category: dict[str, int] = {}
    rows = session.exec(
        select(ViolationType.category, func.count(Finding.id))
        .join(Finding, Finding.violation_type_id == ViolationType.id)
        .group_by(ViolationType.category)
    ).all()
    for category, count in rows:
        findings_by_category[category] = count

    return StatsOut(
        total_studies=total, analyzed_studies=analyzed, qualitative=qualitative,
        non_qualitative=non_qualitative, reviewed=reviewed, pending_review=pending_review,
        findings_by_category=findings_by_category,
    )
