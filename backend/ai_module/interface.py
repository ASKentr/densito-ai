"""
Контракт AI-модуля. Backend знает только эти структуры и функцию `analyze_study` —
благодаря этому реализацию `heuristics.py` в будущем можно заменить на настоящую
обученную модель (или на internal вызов внешнего inference-сервиса), не трогая
backend и веб-интерфейс (см. п.8 ТЗ: "Архитектура должна позволять в дальнейшем
менять или обновлять AI-модель без существенной переработки веб-интерфейса").
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AIFinding:
    image_index: int          # индекс изображения в списке, переданном в analyze_study
    violation_code: str       # код из справочника ViolationType
    severity: str             # low | medium | high | critical
    confidence: float         # 0..1 — уверенность алгоритма
    bbox: tuple[float, float, float, float] | None  # (x, y, w, h), нормализовано 0..1
    explanation: str = ""     # человекочитаемое объяснение (для карточки исследования)


@dataclass
class AIImageReport:
    image_index: int
    quality_score: float          # 0..100, по этому изображению
    anatomical_region: str = ""   # "spine" | "hip" | "" (не удалось определить)
    metrics: dict = field(default_factory=dict)  # сырые метрики для отладки/аудита


@dataclass
class AIStudyResult:
    overall_verdict: str          # "qualitative" | "non_qualitative"
    overall_score: float          # 0..100
    model_version: str
    findings: list[AIFinding] = field(default_factory=list)
    image_reports: list[AIImageReport] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


MODEL_VERSION = "heuristic-cv-0.1"
