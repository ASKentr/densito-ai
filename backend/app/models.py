"""
Схема БД (SQLModel = SQLAlchemy + Pydantic).

Ключевые сущности, согласно ТЗ:
- User            — пользователи, роли admin/expert
- Study           — исследование (одна DICOM-загрузка/один пациент/дата)
- StudyImage      — один DICOM-файл/кадр внутри исследования (в ZIP может быть несколько)
- ViolationType   — редактируемый администратором справочник типов нарушений
- Finding         — конкретное нарушение на конкретном изображении: от AI и/или от эксперта
- AnalysisResult  — итоговый результат AI-анализа исследования (агрегат по Finding)
- AuditLogEntry   — журнал действий пользователей
"""
import enum
from datetime import datetime
from typing import List, Optional

from sqlmodel import SQLModel, Field, Relationship
from sqlalchemy import Column, JSON, UniqueConstraint


class Role(str, enum.Enum):
    admin = "admin"
    expert = "expert"


class StudyStatus(str, enum.Enum):
    uploaded = "uploaded"
    processing = "processing"
    analyzed = "analyzed"
    error = "error"


class ReviewStatus(str, enum.Enum):
    not_reviewed = "not_reviewed"
    in_review = "in_review"
    reviewed = "reviewed"


class OverallVerdict(str, enum.Enum):
    qualitative = "qualitative"       # качественное исследование
    non_qualitative = "non_qualitative"  # есть нарушения качества


class FindingSource(str, enum.Enum):
    ai = "ai"
    expert = "expert"


class FindingStatus(str, enum.Enum):
    # для находок AI:
    pending = "pending"        # эксперт ещё не рассматривал
    confirmed = "confirmed"    # эксперт подтвердил
    rejected = "rejected"      # эксперт отклонил (ложное срабатывание)
    modified = "modified"      # эксперт изменил категорию/область
    # для находок, добавленных экспертом:
    added = "added"


class Severity(str, enum.Enum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(index=True, unique=True)
    full_name: str = ""
    password_hash: str
    role: Role = Field(default=Role.expert)
    is_active: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ViolationType(SQLModel, table=True):
    """Справочник нарушений (п.6 ТЗ) — редактируется администратором."""
    id: Optional[int] = Field(default=None, primary_key=True)
    code: str = Field(index=True, unique=True)
    name_ru: str
    category: str  # positioning | field_of_view | artifact | image_quality | roi_segmentation | dicom_technical | phi
    description: str = ""
    default_severity: Severity = Severity.medium
    is_active: bool = True
    is_system: bool = False  # системные типы нельзя удалить, только деактивировать
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Study(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    display_id: str = Field(index=True, unique=True)  # человекочитаемый ID для таблицы/поиска

    patient_pseudo_id: str = ""  # обезличенный идентификатор пациента
    study_date: Optional[str] = None  # DICOM StudyDate as YYYYMMDD, храним как строку
    body_part: Optional[str] = None
    study_description: Optional[str] = None
    modality: Optional[str] = None

    original_filename: str
    source_kind: str = "dicom"  # "dicom" | "zip"

    status: StudyStatus = Field(default=StudyStatus.uploaded)
    review_status: ReviewStatus = Field(default=ReviewStatus.not_reviewed)

    anonymization_ok: Optional[bool] = None
    anonymization_report: dict = Field(default_factory=dict, sa_column=Column(JSON))

    uploaded_by_id: Optional[int] = Field(default=None, foreign_key="user.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    images: List["StudyImage"] = Relationship(back_populates="study")
    analysis: Optional["AnalysisResult"] = Relationship(back_populates="study")


class StudyImage(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    study_id: int = Field(foreign_key="study.id")
    filename: str
    storage_path: str
    sop_instance_uid: Optional[str] = None
    rows: Optional[int] = None
    columns: Optional[int] = None
    frame_index: int = 0

    # заполняются AI-анализом (шаг 6 плана переделки, docs/ПЛАН_ПЕРЕДЕЛКИ.md):
    # anatomical_region — официальное значение (submission_mapping.py, шаг 3),
    # quality_prob — вероятность нарушения [0;1] (ai_module/quality_prob.py).
    anatomical_region: Optional[str] = None
    quality_prob: Optional[float] = None

    study: Study = Relationship(back_populates="images")


class AnalysisResult(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    study_id: int = Field(foreign_key="study.id", unique=True)
    overall_verdict: OverallVerdict
    overall_score: float  # 0..100, эвристический агрегированный балл качества
    model_version: str
    findings_count: int = 0
    created_at: datetime = Field(default_factory=datetime.utcnow)
    raw_json: dict = Field(default_factory=dict, sa_column=Column(JSON))

    study: Study = Relationship(back_populates="analysis")


class Finding(SQLModel, table=True):
    """Одно нарушение (от AI или от эксперта) на конкретном изображении."""
    id: Optional[int] = Field(default=None, primary_key=True)
    study_id: int = Field(foreign_key="study.id")
    image_id: int = Field(foreign_key="studyimage.id")
    violation_type_id: int = Field(foreign_key="violationtype.id")

    source: FindingSource
    status: FindingStatus = Field(default=FindingStatus.pending)
    severity: Severity = Severity.medium
    confidence: Optional[float] = None  # только для source=ai, 0..1

    # нормализованные координаты области (0..1) относительно изображения
    bbox_x: Optional[float] = None
    bbox_y: Optional[float] = None
    bbox_w: Optional[float] = None
    bbox_h: Optional[float] = None

    comment: str = ""
    reviewed_by_id: Optional[int] = Field(default=None, foreign_key="user.id")
    reviewed_at: Optional[datetime] = None

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class AuditLogEntry(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: Optional[int] = Field(default=None, foreign_key="user.id")
    username: str = ""
    action: str
    entity: str = ""
    entity_id: Optional[int] = None
    details: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)
