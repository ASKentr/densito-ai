from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel

from app.models import FindingStatus, OverallVerdict, ReviewStatus, Role, Severity, StudyStatus


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    username: str


class UserOut(BaseModel):
    id: int
    username: str
    full_name: str
    role: Role
    is_active: bool


class UserCreate(BaseModel):
    username: str
    password: str
    full_name: str = ""
    role: Role = Role.expert


class ViolationTypeOut(BaseModel):
    id: int
    code: str
    name_ru: str
    category: str
    description: str
    default_severity: Severity
    is_active: bool
    is_system: bool


class ViolationTypeCreate(BaseModel):
    code: str
    name_ru: str
    category: str
    description: str = ""
    default_severity: Severity = Severity.medium


class ViolationTypeUpdate(BaseModel):
    name_ru: Optional[str] = None
    category: Optional[str] = None
    description: Optional[str] = None
    default_severity: Optional[Severity] = None
    is_active: Optional[bool] = None


class FindingOut(BaseModel):
    id: int
    image_id: int
    violation_type_id: int
    violation_code: str
    violation_name: str
    source: str
    status: FindingStatus
    severity: Severity
    confidence: Optional[float]
    bbox_x: Optional[float]
    bbox_y: Optional[float]
    bbox_w: Optional[float]
    bbox_h: Optional[float]
    comment: str
    reviewed_by: Optional[str] = None


class FindingReviewAction(BaseModel):
    action: str  # "confirm" | "reject" | "modify"
    violation_type_id: Optional[int] = None  # для modify
    severity: Optional[Severity] = None
    comment: Optional[str] = None


class FindingCreate(BaseModel):
    image_id: int
    violation_type_id: int
    severity: Severity = Severity.medium
    bbox_x: Optional[float] = None
    bbox_y: Optional[float] = None
    bbox_w: Optional[float] = None
    bbox_h: Optional[float] = None
    comment: str = ""


class ImageOut(BaseModel):
    id: int
    filename: str
    rows: Optional[int]
    columns: Optional[int]
    frame_index: int
    anatomical_region: Optional[str] = None
    quality_prob: Optional[float] = None


class StudyListItem(BaseModel):
    id: int
    display_id: str
    study_date: Optional[str]
    body_part: Optional[str]
    study_description: Optional[str]
    modality: Optional[str]
    status: StudyStatus
    review_status: ReviewStatus
    overall_verdict: Optional[OverallVerdict] = None
    overall_score: Optional[float] = None
    findings_count: int = 0
    anonymization_ok: Optional[bool] = None
    created_at: datetime


class StudyDetail(StudyListItem):
    original_filename: str
    patient_pseudo_id: str
    anonymization_report: dict
    images: list[ImageOut]
    findings: list[FindingOut]
    model_version: Optional[str] = None


class AuditLogOut(BaseModel):
    id: int
    username: str
    action: str
    entity: str
    entity_id: Optional[int]
    details: str
    created_at: datetime


class StatsOut(BaseModel):
    total_studies: int
    analyzed_studies: int
    qualitative: int
    non_qualitative: int
    reviewed: int
    pending_review: int
    findings_by_category: dict[str, int]
