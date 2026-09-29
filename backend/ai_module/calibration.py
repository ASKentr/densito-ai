"""
Геометрическая калибровка снимков (шаг 2 плана переделки, `docs/ПЛАН_ПЕРЕДЕЛКИ.md`).

Пиксель на этих снимках неквадратный: 1,05 мм по Y (вдоль тела, вдоль строк)
и 0,6 мм по X (поперёк тела, вдоль столбцов) — см. «Разъяснения по вопросам
ЛЦТ_V2.docx», вопрос 1. Тегов `PixelSpacing`/`ImagerPixelSpacing` в датасете
нет ни в одном файле (проверено) — аппарат в наборе один, поэтому константы
применяются напрямую, но код всё равно сначала пробует прочитать тег на
случай другого аппарата в закрытом тесте.

Любая геометрическая эвристика, работающая с расстояниями/углами в пикселях
(наклон оси, метрические критерии ТЗ типа «3 см над большим вертелом» или
«2 см от бокового края»), должна сначала перевести координаты в мм через
эти функции — иначе она выдаёт систематически искажённый результат из-за
анизотропии пикселя (см. `heuristics._tilt_from_vertical_deg`).
"""
from __future__ import annotations

import pydicom

PIXEL_SPACING_Y_MM = 1.05  # вдоль тела (строки, Rows)
PIXEL_SPACING_X_MM = 0.6   # поперёк тела (столбцы, Columns)


def get_pixel_spacing_mm(ds: pydicom.FileDataset) -> tuple[float, float]:
    """(spacing_y_mm, spacing_x_mm) — порядок как в DICOM PixelSpacing
    (сначала межстрочный интервал, потом межколоночный).

    Падает на константы датасета, если в файле нет тега — в этом датасете его
    нет ни в одном исследовании (проверено на обучающей выборке).
    """
    for tag in ("PixelSpacing", "ImagerPixelSpacing"):
        value = getattr(ds, tag, None)
        if value and len(value) == 2:
            try:
                spacing_y, spacing_x = float(value[0]), float(value[1])
                if spacing_y > 0 and spacing_x > 0:
                    return spacing_y, spacing_x
            except (TypeError, ValueError):
                continue
    return PIXEL_SPACING_Y_MM, PIXEL_SPACING_X_MM


def px_to_mm(dy_px: float, dx_px: float, spacing_mm: tuple[float, float]) -> tuple[float, float]:
    """Смещение в пикселях (dy — по строкам, dx — по столбцам) -> смещение в мм."""
    spacing_y, spacing_x = spacing_mm
    return dy_px * spacing_y, dx_px * spacing_x


def mm_to_px(dy_mm: float, dx_mm: float, spacing_mm: tuple[float, float]) -> tuple[float, float]:
    """Обратное преобразование: мм -> пиксели (для отрисовки/сравнения bbox)."""
    spacing_y, spacing_x = spacing_mm
    return dy_mm / spacing_y, dx_mm / spacing_x
