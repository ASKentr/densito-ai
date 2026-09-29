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
  - анатомическая область (classify_anatomical_region) -> билатеральная симметрия
    объекта относительно центра кадра: позвоночник симметричен, бедро — нет;
    BodyPartExamined в реальных DICOM всегда пустой, суффиксов имён в закрытом
    тесте не будет (см. docs/ПЛАН_ПЕРЕДЕЛКИ.md, шаг 1), поэтому только по пикселям;
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

from ai_module import learned_scorers
from ai_module.calibration import get_pixel_spacing_mm
from ai_module.dicom_io import (
    auto_windowed_uint8,
    check_phi,
    check_required_tags,
    get_pixel_array,
    read_dicom,
)
from ai_module.interface import AIFinding, AIImageReport, AIStudyResult, MODEL_VERSION

# --- пороги (калибровано на sample_data/generate_synthetic_studies.py) ---
# Резкость/экспозиция не входят в таксономию ТЗ (в выходную таблицу не попадают,
# см. submission_mapping.py) и разметки для них нет — это внутренние проверки для
# веб-интерфейса. Исходные пороги (100 и std 220) подбирались на 12-битной
# синтетике 768 px и срабатывали на 100% реальных снимков: аппарат выдаёт
# 8-битные гладкие снимки ~250 px (var Лапласиана 13.5..~70, std 46..~72).
BLUR_LAPLACIAN_THRESHOLD = 8.0         # ниже -> грубая смазанность (синтетика blur ≈ 0, реальный минимум 13.5)
# std сырых пикселей как доля полного диапазона 2^BitsStored-1: не зависит от
# разрядности. 0.054 ≈ прежние 220/4095; у реальных снимков минимум 0.18,
# у синтетического «недоэкспонированного» 0.039.
RAW_CONTRAST_STD_FRAC_THRESHOLD = 0.054
# Кость у бокового края кадра -> область интереса обрезана. Было 20 px: на реальных
# данных это флагует 14.7% снимков бедра (41 из 279) при частоте нарушения
# ~0.4% (единственный размеченный позитив лежит в 40 px от края — правило его
# всё равно не ловило), то есть почти чистые ложные срабатывания, которые к тому
# же дают quality_class=1. 2 px — только реальный «срез» кости у границы кадра
# (2.9% снимков). Настоящий критерий ТЗ (2 см от края ROI, 3 см сверху/снизу)
# требует положения ROI/большого вертела, которого у нас нет; позитивов для
# калибровки — 1, поэтому правило намеренно консервативное.
EDGE_MARGIN_PX = 2

# Обученные скореры позвоночника (learned_scorers.py); False -> прежняя логика.
USE_LEARNED_SPINE_SCORERS = True
POSITION_OFFSET_FRAC_THRESHOLD = 0.08  # смещение центра объекта от ожидаемого (доля стороны)
TILT_ANGLE_THRESHOLD_DEG = 5.0         # ось позвоночника отклонена от вертикали — критерий ТЗ «до 5°»
                                        # (раньше был 7.0, подобран на synthetic-данных с квадратным
                                        # пикселем; теперь угол считается в мм — см. calibration.py —
                                        # и можно использовать порог ТЗ буквально)
ARTIFACT_BRIGHTNESS_PCTL = 99.2
ARTIFACT_MIN_AREA_PX = 30
ARTIFACT_MAX_AREA_FRAC = 0.03          # крупнее — скорее часть анатомии, не артефакт

# Порог признака билатеральной симметрии (max по двум осям, см.
# classify_anatomical_region) для классификации анатомической области.
# Калибровано на 3 официальных тестовых файлах ("Для теста") + 22
# однозначных исследованиях обучающей выборки (ровно один DICOM-файл в
# study-папке, область не вызывает сомнений) — там разрыв между максимумом
# у бедра (0.20) и минимумом у позвоночника (0.457), порог взят с запасом
# посередине.
REGION_SYMMETRY_THRESHOLD = 0.30

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


def _tilt_from_vertical_deg(mask: np.ndarray, pixel_spacing_mm: tuple[float, float]) -> float:
    """Отклонение главной оси объекта от вертикали, в градусах, >= 0.
    Позвоночный столб в норме вытянут вертикально — то есть отклонение в норме
    близко к 0, а не сам угол PCA-оси (который для вертикального объекта ~90°).

    Пиксель неквадратный (1,05 мм по Y, 0,6 мм по X — см. calibration.py):
    координаты обязательно переводятся в мм перед PCA, иначе угол считается в
    искажённой системе координат. Без этого перевода 1,75-кратное растяжение
    Y относительно X (1.05/0.6) завышает измеренный угол наклона примерно в
    те же ~1,75 раза — например, реальные 5° выглядели бы в пикселях как ~8,7°,
    из-за чего порог ТЗ «до 5°» массово ложно срабатывал бы на нормальных
    снимках."""
    ys, xs = np.where(mask)
    if len(xs) < 10:
        return 0.0
    spacing_y, spacing_x = pixel_spacing_mm
    xs_mm = xs.astype(np.float64) * spacing_x
    ys_mm = ys.astype(np.float64) * spacing_y
    cov = np.cov(np.vstack([xs_mm, ys_mm]))
    evals, evecs = np.linalg.eigh(cov)
    major = evecs[:, np.argmax(evals)]
    angle = np.degrees(np.arctan2(major[1], major[0]))  # угол от горизонтали, [-90, 90]
    return float(abs(abs(angle) - 90))


def _mirror_iou_about_col(mask: np.ndarray, col: float) -> float:
    """IoU маски со своим отражением относительно вертикальной прямой x=col
    (в пикселях кадра; может быть нецелым — тогда столбцы округляются)."""
    h, w = mask.shape
    cols = np.arange(w)
    mirrored_cols = np.round(2 * col - cols).astype(int)
    valid = (mirrored_cols >= 0) & (mirrored_cols < w)
    a = mask[:, valid]
    b = mask[:, mirrored_cols[valid]]
    inter = np.logical_and(a, b).sum()
    union = np.logical_or(a, b).sum()
    return float(inter / union) if union else 0.0


def classify_anatomical_region(main_mask: np.ndarray) -> tuple[str, float, float]:
    """Определяет область по форме объекта на снимке — без DICOM-тегов и без
    суффиксов имён файлов (в закрытом тесте их не будет, разъяснения, вопрос 15;
    BodyPartExamined в реальном датасете всегда пустой — проверено).

    Признак: поясничный отдел позвоночника в норме билатерально симметричен
    относительно вертикальной оси (рёбра, дуги позвонков, крылья подвздошных
    костей — парные структуры по обе стороны позвоночного столба);
    проксимальный отдел бедра — асимметричная структура (головка бедра с одной
    стороны, диафиз уходит по диагонали).

    Симметрия считается относительно ДВУХ кандидатов на ось и берётся лучшая
    (max) — так надёжнее, чем любая ось по отдельности:
      - центр кадра: работает, когда объект снят по центру (обычный случай —
        |смещение центра от центра кадра| <= 0.115 доли ширины на всех
        однозначных реальных исследованиях), но проваливается на снимках с
        сильно смещённым позиционированием (например, найденный при тестах
        синтетический случай shift_x=70px/9° — симметрия относительно центра
        кадра там 0.0, хотя сам объект внутри полностью симметричен);
      - центроид масс объекта: устойчив именно к такому сдвигу целиком, но
        сам по себе не идеален — центр масс может съехать, если в маску
        асимметрично попал кусок соседней анатомии (ребро, крыло таза) с
        одной стороны сильнее другой (это и была причина первой, отброшенной
        версии на bbox-центре — см. историю в git/чате).
    На всех надёжных данных (3 официальных теста + 21 однозначный spine +
    1 однозначный hip) комбинация даёт разрыв 0.20 (макс. hip) / 0.457
    (мин. spine) — заметно больше, чем у одной оси кадра (0.20 / 0.125).

    Возвращает (region, confidence, symmetry) — region: "spine" | "hip".
    """
    h, w = main_mask.shape
    ys, xs = np.where(main_mask)
    sym_frame = _mirror_iou_about_col(main_mask, w / 2.0)
    sym_centroid = _mirror_iou_about_col(main_mask, float(xs.mean())) if len(xs) else 0.0
    symmetry = max(sym_frame, sym_centroid)

    region = "spine" if symmetry >= REGION_SYMMETRY_THRESHOLD else "hip"
    confidence = float(np.clip(0.5 + abs(symmetry - REGION_SYMMETRY_THRESHOLD) * 1.5, 0.5, 0.95))
    return region, confidence, symmetry


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
    bits = int(getattr(ds, "BitsStored", 0) or 0)
    if bits <= 0:
        bits = max(8, int(np.ceil(np.log2(float(arr.max()) + 1))))
    raw_std_frac = raw_std / float(2 ** bits - 1)
    metrics["raw_std_frac"] = raw_std_frac
    if raw_std_frac < RAW_CONTRAST_STD_FRAC_THRESHOLD:
        conf = float(np.clip(1 - raw_std_frac / RAW_CONTRAST_STD_FRAC_THRESHOLD, 0.5, 0.95))
        findings.append(AIFinding(
            image_index=image_index, violation_code="low_image_quality",
            severity="medium", confidence=conf, bbox=None,
            explanation=f"Низкий динамический диапазон сигнала (std = {raw_std_frac:.1%} диапазона "
                        f"{bits} бит, порог {RAW_CONTRAST_STD_FRAC_THRESHOLD:.1%}) — "
                        f"подозрение на недостаточную экспозицию.",
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
        region, region_confidence, region_symmetry = classify_anatomical_region(main_mask)
        metrics["anatomical_region"] = region
        metrics["anatomical_region_confidence"] = region_confidence
        metrics["region_symmetry"] = region_symmetry

        use_learned_spine = (
            region == "spine" and USE_LEARNED_SPINE_SCORERS
            and learned_scorers.available() and learned_scorers.in_domain(rows, cols)
        )
        metrics["learned_spine_scorers"] = use_learned_spine

        if use_learned_spine:
            # Позвоночник в домене обучения (ROI-кропы этого сканера). Наклон
            # считается ВСЕГДА (раньше пропускался, если объект касался бокового
            # края — а рёбра у позвоночника часто доходят до краёв кадра, из-за
            # чего проверка наклона молча не выполнялась на значительной доле
            # снимков). Укладка и посторонние предметы — обученные скореры
            # (learned_scorers.py); прежние проверки для позвоночника (смещение
            # центра — F1 0.00; «яркие компоненты» — F1 0.08; обрезка по краю —
            # такого критерия у позвоночника в ТЗ нет) отключены.
            pixel_spacing_mm = get_pixel_spacing_mm(ds)
            tilt = _tilt_from_vertical_deg(main_mask, pixel_spacing_mm)
            metrics["center_offset_frac"] = abs(bbox["cx"] - cols / 2) / cols
            metrics["pixel_spacing_mm"] = pixel_spacing_mm
            metrics["tilt_from_vertical_deg"] = tilt

            if tilt > TILT_ANGLE_THRESHOLD_DEG:
                findings.append(AIFinding(
                    image_index=image_index, violation_code="spine_axis_misaligned",
                    severity="high", confidence=float(np.clip(0.5 + (tilt - TILT_ANGLE_THRESHOLD_DEG) / 10, 0.5, 0.95)),
                    bbox=(bbox["x0"] / cols, bbox["y0"] / rows,
                          (bbox["x1"] - bbox["x0"]) / cols, (bbox["y1"] - bbox["y0"]) / rows),
                    explanation=f"Отклонение оси позвоночника от вертикали {tilt:.1f}°, "
                                f"порог ТЗ {TILT_ANGLE_THRESHOLD_DEG:.0f}°.",
                ))

            scores = learned_scorers.score_spine(learned_scorers.spine_features(img8, main_mask))
            metrics["spine_p_pos"] = scores["pos"]["p"]
            metrics["spine_p_art"] = scores["art"]["p"]
            metrics["spine_p_any"] = scores["any"]["p"]
            if scores["pos"]["flag"]:
                findings.append(AIFinding(
                    image_index=image_index, violation_code="incorrect_positioning",
                    severity="high", confidence=scores["pos"]["p"], bbox=None,
                    explanation="Признаки некорректной укладки: по оценке обученной модели нижняя часть "
                                "кадра (верхние края подвздошных костей) выражена слабее нормы "
                                f"(вероятность нарушения {scores['pos']['p']:.2f}).",
                ))
            if scores["art"]["flag"]:
                findings.append(AIFinding(
                    image_index=image_index, violation_code="artifact",
                    severity="medium", confidence=scores["art"]["p"], bbox=None,
                    explanation="Признаки посторонних предметов/наложений в верхней части кадра по "
                                f"оценке обученной модели (вероятность {scores['art']['p']:.2f}).",
                ))
            touches_edge = False  # ниже — прежняя логика только для бедра/вне домена
        else:
            touches_edge = bbox["x0"] <= EDGE_MARGIN_PX or bbox["x1"] >= cols - 1 - EDGE_MARGIN_PX

        if use_learned_spine:
            pass
        elif touches_edge:
            findings.append(AIFinding(
                image_index=image_index, violation_code="incomplete_field_of_view",
                severity="high", confidence=0.75,
                bbox=(bbox["x0"] / cols, bbox["y0"] / rows,
                      (bbox["x1"] - bbox["x0"]) / cols, (bbox["y1"] - bbox["y0"]) / rows),
                explanation="Анатомическая область касается края снимка — часть исследуемой "
                            "зоны, вероятно, обрезана.",
            ))
        else:
            offset_frac = abs(bbox["cx"] - cols / 2) / cols
            # Наклон как признак неправильного позиционирования осмысленен только
            # для структур, которые в норме ориентированы предсказуемо (например,
            # позвоночный столб — вертикально). У тазобедренного сустава шейка
            # бедра в норме идёт по диагонали — угол здесь ничего не говорит о
            # позиционировании пациента, поэтому для HIP наклон не проверяем.
            pixel_spacing_mm = get_pixel_spacing_mm(ds)
            tilt = _tilt_from_vertical_deg(main_mask, pixel_spacing_mm) if region == "spine" else 0.0
            metrics["center_offset_frac"] = offset_frac
            metrics["pixel_spacing_mm"] = pixel_spacing_mm
            metrics["tilt_from_vertical_deg"] = tilt

            region_bbox = (bbox["x0"] / cols, bbox["y0"] / rows,
                           (bbox["x1"] - bbox["x0"]) / cols, (bbox["y1"] - bbox["y0"]) / rows)

            # Смещение центра -> некорректная укладка (для обеих областей).
            if offset_frac > POSITION_OFFSET_FRAC_THRESHOLD:
                findings.append(AIFinding(
                    image_index=image_index, violation_code="incorrect_positioning",
                    severity="high", confidence=float(np.clip(0.5 + offset_frac * 2, 0.5, 0.95)),
                    bbox=region_bbox,
                    explanation=f"Смещение центра анатомической области от центра поля снимка "
                                f"на {offset_frac * 100:.1f}% ширины.",
                ))

            # Наклон оси -> отдельное нарушение (только для позвоночника, см. выше).
            if tilt > TILT_ANGLE_THRESHOLD_DEG:
                findings.append(AIFinding(
                    image_index=image_index, violation_code="spine_axis_misaligned",
                    severity="high", confidence=float(np.clip(0.5 + (tilt - TILT_ANGLE_THRESHOLD_DEG) / 10, 0.5, 0.95)),
                    bbox=region_bbox,
                    explanation=f"Отклонение оси позвоночника от вертикали {tilt:.1f}°, "
                                f"порог ТЗ {TILT_ANGLE_THRESHOLD_DEG:.0f}°.",
                ))

        # --- артефакты (прежний детектор ярких компонентов: бедро / вне домена) ---
        for art in ([] if use_learned_spine else _detect_artifacts(img8, main_mask)):
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
    return AIImageReport(
        image_index=image_index, quality_score=quality_score,
        anatomical_region=metrics.get("anatomical_region", ""), metrics=metrics,
    ), findings


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
