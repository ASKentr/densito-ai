"""
Сборка обучающего датасета для CNN-модели (шаг 4 плана переделки,
`docs/ПЛАН_ПЕРЕДЕЛКИ.md`). Разметка (`labels.py`) даёт метки на уровне
исследования и области, а не снимка — этот модуль сводит их к обучающим
примерам на уровне снимка:

  - область снимка (spine/hip) берётся из эвристики `classify_anatomical_region`
    (шаг 1) — независимого способа определить регион снимка без разметки нет;
  - для spine-снимка метки берутся из строки `spine`, если область в xlsx
    вообще оценена (иначе пример пропускается — метки нет);
  - для hip-снимка сторона (левое/правое бедро) неизвестна — надёжного
    L/R-классификатора не строили. Если `hip_right`/`hip_left` совпадают по
    полю — берём общее значение (85% исследований с двумя оценённыи бёдрами
    совпадают). Если они расходятся — пример для ЭТОГО поля пропускается
    (лучше меньше данных, чем заведомо неверная метка).
"""
from __future__ import annotations

import pathlib
import warnings
from dataclasses import dataclass, field as dc_field

import numpy as np

from ai_module.dicom_io import auto_windowed_uint8, get_pixel_array, read_dicom
from ai_module.heuristics import _segment_main_object, classify_anatomical_region
from ai_module.labels import StudyLabel, load_labels

SPINE_FIELDS = ("incorrect_positioning", "axis_misaligned", "artifact")
HIP_FIELDS = ("incorrect_positioning", "incomplete_field_of_view")


@dataclass
class ImageExample:
    study: str
    path: str
    region: str                      # "spine" | "hip"
    labels: dict[str, bool]          # только поля, для которых есть надёжная метка
    bbox: tuple[int, int, int, int]  # y0, y1, x0, x1 — главный объект, для кропа


def _resolve_hip_label(lbl: StudyLabel, hip_field: str) -> bool | None:
    r = lbl["hip_right"][hip_field]
    l = lbl["hip_left"][hip_field]
    if r is None and l is None:
        return None
    if r is None:
        return l
    if l is None:
        return r
    return r if r == l else None  # расходятся -> ненадёжно


def build_examples(xlsx_path: str, studies_dir: str) -> list[ImageExample]:
    labels = load_labels(xlsx_path)
    studies_dir_p = pathlib.Path(studies_dir)
    examples: list[ImageExample] = []

    warnings.filterwarnings("ignore")  # невалидные UID в тегах, не относится к пикселям
    for study, lbl in labels.items():
        study_dir = studies_dir_p / study
        if not study_dir.is_dir():
            continue
        for dcm in sorted(study_dir.rglob("*.dcm")):
            try:
                ds = read_dicom(str(dcm))
                arr = get_pixel_array(ds)
                img8 = auto_windowed_uint8(arr)
                mask, bbox = _segment_main_object(img8)
            except Exception:
                continue
            if bbox is None:
                continue
            region, _, _ = classify_anatomical_region(mask)

            if region == "spine":
                spine = lbl["spine"]
                if all(spine[f] is None for f in SPINE_FIELDS):
                    continue  # позвоночник в этом исследовании не оценивался
                example_labels = {f: spine[f] for f in SPINE_FIELDS if spine[f] is not None}
            else:
                example_labels = {}
                for f in HIP_FIELDS:
                    v = _resolve_hip_label(lbl, f)
                    if v is not None:
                        example_labels[f] = v
                if not example_labels:
                    continue

            examples.append(ImageExample(
                study=study, path=str(dcm), region=region,
                labels=example_labels,
                bbox=(bbox["y0"], bbox["y1"], bbox["x0"], bbox["x1"]),
            ))
    return examples


def summarize(examples: list[ImageExample]) -> str:
    lines = [f"всего примеров: {len(examples)}", f"уникальных исследований: {len({e.study for e in examples})}"]
    for region, fields in (("spine", SPINE_FIELDS), ("hip", HIP_FIELDS)):
        region_examples = [e for e in examples if e.region == region]
        lines.append(f"\nregion={region}: {len(region_examples)} снимков")
        for f in fields:
            values = [e.labels[f] for e in region_examples if f in e.labels]
            pos = sum(values)
            lines.append(f"  {f}: n={len(values)} positive={pos} negative={len(values) - pos}")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys

    xlsx = sys.argv[1] if len(sys.argv) > 1 else "разметка.xlsx"
    studies = sys.argv[2] if len(sys.argv) > 2 else "Исследования"
    exs = build_examples(xlsx, studies)
    print(summarize(exs))
