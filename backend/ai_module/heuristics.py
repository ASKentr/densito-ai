"""
Эвристический AI-движок оценки качества денситометрии (реализация контракта
из interface.py). Без обученной модели и без датасета (он появится позже) —
но выполняет реальный анализ пикселей, а не заглушку с фиксированным ответом.

Что реально считается:
  - резкость (variance of Laplacian) -> low_image_quality (смазанность);
  - экспозиция/контраст по "сырым" значениям пикселей -> low_image_quality;
  - главный анатомический объект (сегментация по яркости + связные компоненты) ->
    его bounding box и центр масс используются для:
      * incomplete_field_of_view (объект касается края поля снимка),
      * incorrect_positioning (смещение/поворот относительно ожидаемого положения);
  - "лишние" яркие связные компоненты вне главного объекта -> artifact;
  - обязательные DICOM-теги -> dicom_technical_error;
  - персональные данные в теге -> phi_not_removed.

roi_segmentation_error пока НЕ вычисляется по-настоящему: для сравнения
разметки нужен эталон (экспертная разметка), которого в исходных данных ещё
нет. Точка расширения помечена ниже (compare_roi_with_reference) — как только
появятся реальные размеченные исследования, там нужно реализовать сравнение
(например IoU между ROI модели и ROI эксперта) и включить код в analyze_image.

Все пороги — эвристические, подобраны на синтетических примерах в
sample_data/. Это ожидаемо: цель первой версии — рабочий, объяснимый и
переключаемый (см. interface.py) AI-модуль, а не финальная точность.
"""
from __future__ import annotations

import cv2
import numpy as np
from scipy import ndimage

from ai_module.dicom_io import (
    auto_windowed_uint8,
    check_phi,
    check_required_tags,
    get_pixel_array,
    read_dicom,
)
from ai_module.interface import AIFinding, AIImageReport, AIStudyResult, MODEL_VERSION

# --- пороги (калибровано на sample_data/generate_synthetic_studies.py) ---
BLUR_LAPLACIAN_THRESHOLD = 100.0       # ниже -> подозрение на смазанность
RAW_CONTRAST_STD_THRESHOLD = 220.0     # ниже -> подозрение на низкую экспозицию/контраст
EDGE_MARGIN_PX = 20                    # объект ближе этого к краю -> обрезан
POSITION_OFFSET_FRAC_THRESHOLD = 0.08  # смещение центра объекта от ожидаемого (доля стороны)
TILT_ANGLE_THRESHOLD_DEG = 7.0         # отклонение от вертикали для вытянутых вдоль тела структур (позвоночник)
ARTIFACT_BRIGHTNESS_PCTL = 99.2
ARTIFACT_MIN_AREA_PX = 30
ARTIFACT_MAX_AREA_FRAC = 0.03          # крупнее — скорее часть анатомии, не артефакт

SEVERITY_PENALTY = {"low": 4, "medium": 8, "high": 15, "critical": 25}


def _segment_main_object(img8: np.ndarray) -> tuple[np.ndarray, dict | None]:
    # Otsu вместо фиксированного перцентиля: доля "костной" площади в кадре
    # сильно различается между исследованиями (обрезанный снимок, разный масштаб
    # укладки), а Otsu адаптивно находит границу между фоном и объектом.
    otsu_thr, _ = cv2.threshold(img8, 0, 255, cv2.THRESH_OTSU)
    mask = img8 > otsu_thr
    mask = ndimage.binary_opening(mask, structure=np.ones((3, 3)))
    # соседние анатомические фрагменты (например, тела соседних позвонков с
    # небольшим зазором между ними) объединяем в один объект перед тем, как
    # искать связные компоненты — иначе каждый позвонок посчитается отдельно.
    merged = ndimage.binary_dilation(mask, iterations=7)
    labeled, n = ndimage.label(merged)
    if n == 0:
        return mask, None
    sizes = ndimage.sum(merged, labeled, range(1, n + 1))
    main_label = int(np.argmax(sizes)) + 1
    main_mask = (labeled == main_label) & mask  # bbox/центр считаем по исходной (не раздутой) маске
    if not main_mask.any():
        main_mask = labeled == main_label
    ys, xs = np.where(main_mask)
    bbox = {
        "y0": int(ys.min()), "y1": int(ys.max()),
        "x0": int(xs.min()), "x1": int(xs.max()),
        "cy": float(ys.mean()), "cx": float(xs.mean()),
    }
    return main_mask, bbox


def _tilt_from_vertical_deg(mask: np.ndarray) -> float:
    """Отклонение главной оси объекта от вертикали, в градусах, >= 0.
    Позвоночный столб в норме вытянут вертикально — то есть отклонение в норме
    близко к 0, а не сам угол PCA-оси (который для вертикального объекта ~90°)."""
    ys, xs = np.where(mask)
    if len(xs) < 10:
        return 0.0
    cov = np.cov(np.vstack([xs, ys]).astype(np.float64))
    evals, evecs = np.linalg.eigh(cov)
    major = evecs[:, np.argmax(evals)]
    angle = np.degrees(np.arctan2(major[1], major[0]))  # угол от горизонтали, [-90, 90]
    return float(abs(abs(angle) - 90))


def _detect_artifacts(img8: np.ndarray, main_mask: np.ndarray) -> list[dict]:
    rows, cols = img8.shape
    thr = min(np.percentile(img8, ARTIFACT_BRIGHTNESS_PCTL), 240)
    bright = img8 >= thr
    candidate = bright & ~ndimage.binary_dilation(main_mask, iterations=4)
    labeled, n = ndimage.label(candidate)
    results = []
    for i in range(1, n + 1):
        comp = labeled == i
        area = int(comp.sum())
        if area < ARTIFACT_MIN_AREA_PX or area > ARTIFACT_MAX_AREA_FRAC * rows * cols:
            continue
        ys, xs = np.where(comp)
        results.append({
            "y0": int(ys.min()), "y1": int(ys.max()),
            "x0": int(xs.min()), "x1": int(xs.max()),
            "area": area,
        })
    return results


def analyze_image(image_index: int, path: str) -> tuple[AIImageReport, list[AIFinding]]:
    findings: list[AIFinding] = []
    metrics: dict = {}

    try:
        ds = read_dicom(path)
    except Exception as exc:
        findings.append(AIFinding(
            image_index=image_index, violation_code="dicom_technical_error",
            severity="critical", confidence=1.0, bbox=None,
            explanation=f"Файл не удалось прочитать как DICOM: {exc}",
        ))
        return AIImageReport(image_index=image_index, quality_score=0.0, metrics=metrics), findings

    missing_tags = check_required_tags(ds)
    if missing_tags:
        findings.append(AIFinding(
            image_index=image_index, violation_code="dicom_technical_error",
            severity="critical", confidence=1.0, bbox=None,
            explanation="Отсутствуют обязательные DICOM-теги: " + ", ".join(missing_tags),
        ))

    phi_tags = check_phi(ds)
    if phi_tags:
        findings.append(AIFinding(
            image_index=image_index, violation_code="phi_not_removed",
            severity="critical", confidence=1.0, bbox=None,
            explanation="Обнаружены необезличенные персональные данные в тегах: " + ", ".join(phi_tags),
        ))

    if "PixelData" not in ds or "Rows" not in ds or "Columns" not in ds:
        return AIImageReport(image_index=image_index, quality_score=0.0, metrics=metrics), findings

    try:
        arr = get_pixel_array(ds)
    except Exception as exc:
        findings.append(AIFinding(
            image_index=image_index, violation_code="dicom_technical_error",
            severity="critical", confidence=0.9, bbox=None,
            explanation=f"Не удалось декодировать PixelData: {exc}",
        ))
        return AIImageReport(image_index=image_index, quality_score=0.0, metrics=metrics), findings

    rows, cols = arr.shape[-2], arr.shape[-1]
    img8 = auto_windowed_uint8(arr)

    # --- резкость ---
    lap_var = float(cv2.Laplacian(img8, cv2.CV_64F).var())
    metrics["laplacian_var"] = lap_var
    if lap_var < BLUR_LAPLACIAN_THRESHOLD:
        conf = float(np.clip(1 - lap_var / BLUR_LAPLACIAN_THRESHOLD, 0.5, 0.97))
        findings.append(AIFinding(
            image_index=image_index, violation_code="low_image_quality",
            severity="medium", confidence=conf, bbox=None,
            explanation=f"Низкая резкость изображения (var Лапласиана = {lap_var:.0f}, "
                        f"порог {BLUR_LAPLACIAN_THRESHOLD:.0f}) — похоже на смазанность/движение.",
        ))

    # --- экспозиция/контраст (по сырым значениям, а не по авто-нормализованным) ---
    raw_std = float(arr.std())
    metrics["raw_std"] = raw_std
    if raw_std < RAW_CONTRAST_STD_THRESHOLD:
        conf = float(np.clip(1 - raw_std / RAW_CONTRAST_STD_THRESHOLD, 0.5, 0.95))
        findings.append(AIFinding(
            image_index=image_index, violation_code="low_image_quality",
            severity="medium", confidence=conf, bbox=None,
            explanation=f"Низкий динамический диапазон сигнала (std = {raw_std:.0f}, "
                        f"порог {RAW_CONTRAST_STD_THRESHOLD:.0f}) — подозрение на недостаточную экспозицию.",
        ))

    # --- сегментация главного объекта: позиционирование / обрезка ---
    main_mask, bbox = _segment_main_object(img8)
    if bbox is None:
        findings.append(AIFinding(
            image_index=image_index, violation_code="incomplete_field_of_view",
            severity="high", confidence=0.6, bbox=None,
            explanation="Не удалось выделить анатомическую структуру на изображении.",
        ))
    else:
        touches_edge = (
            bbox["x0"] <= EDGE_MARGIN_PX or bbox["x1"] >= cols - 1 - EDGE_MARGIN_PX or
            bbox["y0"] <= EDGE_MARGIN_PX or bbox["y1"] >= rows - 1 - EDGE_MARGIN_PX
        )
        if touches_edge:
            findings.append(AIFinding(
                image_index=image_index, violation_code="incomplete_field_of_view",
                severity="high", confidence=0.75,
                bbox=(bbox["x0"] / cols, bbox["y0"] / rows,
                      (bbox["x1"] - bbox["x0"]) / cols, (bbox["y1"] - bbox["y0"]) / rows),
                explanation="Анатомическая область касается края снимка — часть исследуемой "
                            "зоны, вероятно, обрезана.",
            ))
        else:
            body_part = str(getattr(ds, "BodyPartExamined", "") or "").upper()
            offset_frac = abs(bbox["cx"] - cols / 2) / cols
            # Наклон как признак неправильного позиционирования осмысленен только
            # для структур, которые в норме ориентированы предсказуемо (например,
            # позвоночный столб — вертикально). У тазобедренного сустава шейка
            # бедра в норме идёт по диагонали — угол здесь ничего не говорит о
            # позиционировании пациента, поэтому для HIP наклон не проверяем.
            tilt = _tilt_from_vertical_deg(main_mask) if "SPINE" in body_part else 0.0
            metrics["center_offset_frac"] = offset_frac
            metrics["tilt_from_vertical_deg"] = tilt
            if offset_frac > POSITION_OFFSET_FRAC_THRESHOLD or tilt > TILT_ANGLE_THRESHOLD_DEG:
                findings.append(AIFinding(
                    image_index=image_index, violation_code="incorrect_positioning",
                    severity="high", confidence=float(np.clip(0.5 + offset_frac * 2, 0.5, 0.95)),
                    bbox=(bbox["x0"] / cols, bbox["y0"] / rows,
                          (bbox["x1"] - bbox["x0"]) / cols, (bbox["y1"] - bbox["y0"]) / rows),
                    explanation=f"Смещение центра анатомической области от центра поля снимка "
                                f"на {offset_frac * 100:.1f}% ширины, отклонение от вертикали {tilt:.1f}°.",
                ))

        # --- артефакты ---
        for art in _detect_artifacts(img8, main_mask):
            findings.append(AIFinding(
                image_index=image_index, violation_code="artifact",
                severity="medium", confidence=0.7,
                bbox=(art["x0"] / cols, art["y0"] / rows,
                      (art["x1"] - art["x0"]) / cols, (art["y1"] - art["y0"]) / rows),
                explanation=f"Обнаружен посторонний яркий объект в поле снимка "
                            f"(площадь {art['area']} px), не относящийся к основной анатомии.",
            ))

    # --- точка расширения: сравнение ROI с эталонной экспертной разметкой ---
    # compare_roi_with_reference(main_mask, reference_roi) -> добавит findings с
    # violation_code="roi_segmentation_error", когда появятся размеченные данные.

    penalty = sum(SEVERITY_PENALTY.get(f.severity, 5) for f in findings)
    quality_score = float(np.clip(100 - penalty, 0, 100))
    return AIImageReport(image_index=image_index, quality_score=quality_score, metrics=metrics), findings


def analyze_study(image_paths: list[str]) -> AIStudyResult:
    all_findings: list[AIFinding] = []
    image_reports: list[AIImageReport] = []

    for idx, path in enumerate(image_paths):
        report, findings = analyze_image(idx, path)
        image_reports.append(report)
        all_findings.extend(findings)

    overall_score = float(np.mean([r.quality_score for r in image_reports])) if image_reports else 0.0
    # Любое найденное нарушение (кроме low) переводит исследование в "с нарушениями" —
    # итоговый score остаётся информативным показателем степени серьёзности.
    has_defect = any(f.severity != "low" for f in all_findings)
    overall_verdict = "non_qualitative" if (has_defect or overall_score < 90) else "qualitative"

    notes = []
    if not image_reports:
        notes.append("В исследовании не найдено ни одного читаемого изображения.")

    return AIStudyResult(
        overall_verdict=overall_verdict,
        overall_score=round(overall_score, 1),
        model_version=MODEL_VERSION,
        findings=all_findings,
        image_reports=image_reports,
        notes=notes,
    )
