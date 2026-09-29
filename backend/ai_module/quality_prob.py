"""
Вероятность нарушения [0;1] для колонки `quality_prob` (шаг 5 плана
переделки) — общая для `batch_predict.py` (CLI) и веб-сервиса
(`app/routers/studies.py`, шаг 6), чтобы не считать по-разному в двух местах.

НЕ инверсия `AIStudyResult.overall_score` — туда намешаны low_image_quality/
dicom_technical_error/phi_not_removed, которых нет в официальной таксономии
(`submission_mapping.py`), и это дало бы шумный/нечестный ROC-AUC.
"""
from __future__ import annotations

import numpy as np

from ai_module import heuristics
from ai_module.interface import AIFinding

# Коды нарушений, входящие в официальный violation_type для каждой области
# (см. submission_mapping.py, шаг 3) — только по ним считаем вероятность.
_REGION_RELEVANT_CODES = {
    "spine": {"incorrect_positioning", "spine_axis_misaligned", "artifact"},
    "hip": {"incorrect_positioning", "incomplete_field_of_view"},
}


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + np.exp(-x))


def compute_quality_prob(region: str, metrics: dict, findings: list[AIFinding]) -> float:
    """Вероятностное ИЛИ по применимым для региона чекам: непрерывная метрика
    (смещение центра, наклон оси) нормируется сигмоидой вокруг того же
    порога, что использует бинарное решение в heuristics.py (порог -> 0.5),
    поэтому quality_class и quality_prob согласованы. Для чеков без готовой
    непрерывной метрики (артефакт, область интереса бедра) используется сам
    факт находки как суррогат.
    """
    relevant = _REGION_RELEVANT_CODES.get(region, set())
    if not relevant:
        return 0.0

    # Позвоночник в домене обучения: вероятность «есть хоть одно нарушение» из
    # обученной логистики (learned_scorers.py, CV ROC-AUC 0.64 [0.52, 0.81]
    # против 0.45 [0.30, 0.60] у прежней комбинации порогов). Наклон оси в неё
    # намеренно не входит: на CV он не улучшал итоговую метрику (слабо связан
    # с «любым нарушением»), но остаётся бинарным критерием ТЗ в violation_type.
    if region == "spine" and "spine_p_any" in metrics:
        return float(np.clip(metrics["spine_p_any"], 0.0, 1.0))

    scores: list[float] = []
    if region == "hip" and "cnn_positioning_prob" in metrics:
        # Гибридный режим (hybrid.py): укладку бедра оценивает CNN (OOF ROC-AUC
        # 0.85 [0.74, 0.93] против 0.55 [0.39, 0.69] у смещения центра) — она же
        # определяет quality_prob, а не прежняя сигмоида смещения.
        scores.append(float(metrics["cnn_positioning_prob"]))
    elif "incorrect_positioning" in relevant and "center_offset_frac" in metrics:
        scores.append(_sigmoid((metrics["center_offset_frac"] - heuristics.POSITION_OFFSET_FRAC_THRESHOLD) / 0.03))
    if "spine_axis_misaligned" in relevant and "tilt_from_vertical_deg" in metrics:
        scores.append(_sigmoid((metrics["tilt_from_vertical_deg"] - heuristics.TILT_ANGLE_THRESHOLD_DEG) / 1.5))

    found_codes = {f.violation_code for f in findings if f.violation_code in relevant}
    for code in ("artifact", "incomplete_field_of_view"):
        if code in relevant:
            scores.append(0.8 if code in found_codes else 0.15)

    if not scores:
        return 0.0
    prob_none = 1.0
    for s in scores:
        prob_none *= (1.0 - s)
    return float(np.clip(1.0 - prob_none, 0.0, 1.0))
