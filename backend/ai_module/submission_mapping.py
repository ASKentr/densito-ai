"""
Маппинг внутренних кодов нарушений на официальные значения для сдаваемой
таблицы (шаг 3 плана переделки, `docs/ПЛАН_ПЕРЕДЕЛКИ.md`).

Внутренний справочник (`violation_catalog.py`) не меняется — он нужен UI и
экспертной разметке. Наружу отдаётся ТОЛЬКО через этот слой, на 5 официальных
формулировок (дословно, «Разъяснения по вопросам ЛЦТ_V2.docx», вопрос 6).
Коды, не входящие в мапы ниже (`low_image_quality`, `dicom_technical_error`,
`phi_not_removed`, `roi_segmentation_error`), в `violation_type` никогда не
попадают — они там просто не определены, а не отфильтрованы отдельной проверкой.
"""
from __future__ import annotations

from typing import Iterable

# Порядок ключей — канонический порядок вывода в violation_type при нескольких
# нарушениях (соответствие с колонками разметки — см. план, шаг 3).
SPINE_VIOLATION_MAP = {
    "incorrect_positioning": "Некорректная укладка",           # xlsx: C
    "spine_axis_misaligned": "Не выравнена ось позвоночника",  # xlsx: D
    "artifact": "Присутствуют посторонние предметы",           # xlsx: E
}

HIP_VIOLATION_MAP = {
    "incorrect_positioning": "Некорректная укладка",            # xlsx: F/H
    "incomplete_field_of_view": "Некорректная область интереса",  # xlsx: G/I
}

REGION_MAP = {
    "spine": "Поясничный отдел позвоночника",
    "hip": "Проксимальный отдел бедра",
}


def map_anatomical_region(region: str) -> str:
    """Внутренний код региона ("spine"/"hip") -> официальное значение
    `anatomical_region`. Сторона (лево/право) наружу не передаётся (не имеет
    значения по условиям задачи)."""
    return REGION_MAP.get(region, "")


def map_violation_types(region: str, violation_codes: Iterable[str]) -> str:
    """Внутренние коды нарушений одного изображения -> строка `violation_type`.

    Несколько нарушений соединяются `;`; при отсутствии — пустая строка
    (не "нет", не "—", не None — см. принцип 3 плана переделки).
    """
    code_map = SPINE_VIOLATION_MAP if region == "spine" else HIP_VIOLATION_MAP if region == "hip" else {}
    present = set(violation_codes)
    officials = [official for code, official in code_map.items() if code in present]
    return ";".join(officials)
