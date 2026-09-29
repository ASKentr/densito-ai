"""
Генератор синтетических DICOM-исследований для демонстрации сервиса.

Реальных обезличенных денситометрических DICOM с экспертной разметкой у нас
пока нет (заказчик добавит их позже). Чтобы можно было прогнать весь сценарий
(загрузка -> AI-анализ -> результат -> вьюер -> экспертная проверка) уже сейчас,
этот скрипт генерирует набор синтетических изображений, имитирующих денситометрию
(DXA) поясничного отдела позвоночника и тазобедренного сустава, с встроенными
дефектами качества (или без них). Формат, теги и структура PixelData полностью
соответствуют DICOM, поэтому пайплайн (парсинг, обезличивание, вьюер, эвристики
качества) отрабатывает на них по-настоящему, а не в виде заглушки.

Дополнительно копируются пара НАСТОЯЩИХ DICOM-файлов из тестового набора pydicom
(CT/MR), чтобы проверить, что парсер не ломается на реальных файлах сторонних
производителей (другой Transfer Syntax, другой набор тегов и т.д.).

Как только появятся настоящие обезличенные исследования — их нужно просто
положить в sample_data/real_studies/ и не запускать этот скрипт вовсе (AI-модуль
и backend с ними никак не связаны, они просто читают DICOM per спецификации).
"""
from __future__ import annotations

import json
import os
import shutil
import zipfile
from dataclasses import dataclass, field

import numpy as np
import pydicom
from pydicom.dataset import FileDataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, generate_uid

OUT_DIR = os.path.join(os.path.dirname(__file__), "studies")
REAL_DIR = os.path.join(os.path.dirname(__file__), "real_studies")

RNG = np.random.default_rng(42)


def _base_canvas(rows: int, cols: int, bg_level: int = 300) -> np.ndarray:
    """Мягкий фон мягких тканей + шум, как на реальном денситометрическом снимке."""
    canvas = np.full((rows, cols), bg_level, dtype=np.float64)
    noise = RNG.normal(0, 15, size=(rows, cols))
    canvas += noise
    # плавный виньетинг по краям поля облучения
    yy, xx = np.mgrid[0:rows, 0:cols]
    cy, cx = rows / 2, cols / 2
    dist = np.sqrt(((yy - cy) / (rows / 2)) ** 2 + ((xx - cx) / (cols / 2)) ** 2)
    canvas *= np.clip(1.15 - 0.25 * dist**2, 0.55, 1.15)
    return canvas


def _draw_spine(canvas: np.ndarray, cx: float, cy_top: float, n_vertebrae: int = 4,
                 scale: float = 1.0, bone_level: float = 2400) -> list[dict]:
    """Рисует L1-L4 условными прямоугольниками и возвращает bbox каждого позвонка (для ROI)."""
    rows, cols = canvas.shape
    boxes = []
    vh = 60 * scale
    vw = 95 * scale
    gap = 8 * scale
    y = cy_top
    for i in range(n_vertebrae):
        x0, x1 = int(cx - vw / 2), int(cx + vw / 2)
        y0, y1 = int(y), int(y + vh)
        x0c, x1c, y0c, y1c = max(0, x0), min(cols, x1), max(0, y0), min(rows, y1)
        if x1c > x0c and y1c > y0c:
            sub = canvas[y0c:y1c, x0c:x1c]
            yy, xx = np.mgrid[0:sub.shape[0], 0:sub.shape[1]]
            edge = np.minimum.reduce([xx, sub.shape[1] - 1 - xx, yy * 2, (sub.shape[0] - 1 - yy) * 2])
            soft = np.clip(edge / 1.5, 0, 1)
            sub += bone_level * soft
            canvas[y0c:y1c, x0c:x1c] = sub
        boxes.append({"label": f"L{i + 1}", "x0": x0, "y0": y0, "x1": x1, "y1": y1})
        y += vh + gap
    return boxes


def _draw_hip(canvas: np.ndarray, cx: float, cy: float, scale: float = 1.0,
              bone_level: float = 2200) -> list[dict]:
    rows, cols = canvas.shape
    yy, xx = np.mgrid[0:rows, 0:cols]
    # головка бедра (эллипс)
    head = (((xx - cx) / (55 * scale)) ** 2 + ((yy - cy) / (55 * scale)) ** 2) <= 1
    # диафиз бедра (наклонная полоса) — тоньше и длиннее головки, как у настоящей
    # кости (иначе фигура получается почти билатерально симметричной и
    # heuristics.classify_anatomical_region путает её с позвоночником, см.
    # docs/ПЛАН_ПЕРЕДЕЛКИ.md, шаг 1/4 — тест test_ai_module.py это отловил).
    neck_len = 260 * scale
    ang = np.deg2rad(35)
    nx, ny = cx + np.cos(ang) * np.arange(0, neck_len), cy + np.sin(ang) * np.arange(0, neck_len)
    neck_mask = np.zeros((rows, cols), dtype=bool)
    for px, py in zip(nx, ny):
        if 0 <= int(py) < rows and 0 <= int(px) < cols:
            r = int(14 * scale)
            y0, y1 = max(0, int(py) - r), min(rows, int(py) + r)
            x0, x1 = max(0, int(px) - r), min(cols, int(px) + r)
            neck_mask[y0:y1, x0:x1] = True
    mask = head | neck_mask
    canvas[mask] += bone_level
    ys, xs = np.where(mask)
    if len(xs) == 0:
        box = {"label": "femur_neck", "x0": int(cx - 60), "y0": int(cy - 60), "x1": int(cx + 60), "y1": int(cy + 60)}
    else:
        box = {"label": "femur_neck", "x0": int(xs.min()), "y0": int(ys.min()), "x1": int(xs.max()), "y1": int(ys.max())}
    return [box]


@dataclass
class Scenario:
    key: str
    title_ru: str
    body_part: str  # "LSPINE" | "HIP"
    defect: str  # code from violation dictionary, or "none"
    expected_verdict: str  # "ok" | "defect"
    expected_category: str | None
    build_kwargs: dict = field(default_factory=dict)


SCENARIOS = [
    Scenario("lspine_ok", "Денситометрия поясничного отдела позвоночника (норма)",
             "LSPINE", "none", "ok", None),
    Scenario("hip_ok", "Денситометрия тазобедренного сустава (норма)",
             "HIP", "none", "ok", None),
    Scenario("lspine_blur", "Поясничный отдел, смазанное изображение (motion artifact)",
             "LSPINE", "low_image_quality", "defect", "low_image_quality",
             {"blur": 4.5}),
    Scenario("lspine_underexposed", "Поясничный отдел, заниженная экспозиция",
             "LSPINE", "low_image_quality", "defect", "low_image_quality",
             {"exposure_scale": 0.35}),
    Scenario("hip_cropped", "Тазобедренный сустав, область исследования обрезана",
             "HIP", "incomplete_field_of_view", "defect", "incomplete_field_of_view",
             {"shift_x": 290}),
    Scenario("lspine_misaligned", "Поясничный отдел, неправильное позиционирование пациента",
             "LSPINE", "incorrect_positioning", "defect", "incorrect_positioning",
             {"shift_x": 70, "tilt": 9}),
    Scenario("hip_artifact", "Тазобедренный сустав, посторонний артефакт в поле снимка",
             "HIP", "artifact", "defect", "artifact",
             {"artifact": True}),
    Scenario("lspine_missing_tags", "Поясничный отдел, технические ошибки DICOM (неполные теги)",
             "LSPINE", "dicom_technical_error", "defect", "dicom_technical_error",
             {"strip_tags": True}),
    Scenario("hip_phi_leak", "Тазобедренный сустав, в файле не обезличены персональные данные",
             "HIP", "phi_not_removed", "defect", "phi_not_removed",
             {"leave_phi": True}),
]


def build_pixels(scenario: Scenario, rows=768, cols=768) -> tuple[np.ndarray, list[dict]]:
    canvas = _base_canvas(rows, cols)
    cx, cy = cols / 2 + scenario.build_kwargs.get("shift_x", 0), rows / 2

    if scenario.body_part == "LSPINE":
        boxes = _draw_spine(canvas, cx, cy - 140, n_vertebrae=4)
    else:
        # Голова бедра рисуется в точке (cx,cy), но диафиз уходит от неё по
        # диагонали — bbox всей фигуры (голова+диафиз) иначе оказывается
        # заметно смещён от центра кадра, хотя в реальных снимках объект
        # всегда близко к центру (см. docs/ПЛАН_ПЕРЕДЕЛКИ.md, шаг 1/4).
        # Сдвигаем точку начала так, чтобы центр итогового bbox совпал с cx,cy.
        boxes = _draw_hip(canvas, cx - 85.5, cy - 53.5)

    if "tilt" in scenario.build_kwargs:
        from scipy import ndimage
        canvas = ndimage.rotate(canvas, scenario.build_kwargs["tilt"], reshape=False, cval=canvas.mean())

    if "blur" in scenario.build_kwargs:
        from scipy import ndimage
        canvas = ndimage.gaussian_filter(canvas, sigma=scenario.build_kwargs["blur"])

    if "exposure_scale" in scenario.build_kwargs:
        mean = canvas.mean()
        canvas = mean + (canvas - mean) * scenario.build_kwargs["exposure_scale"]

    if scenario.build_kwargs.get("artifact"):
        yy, xx = np.mgrid[0:rows, 0:cols]
        ax, ay = cols * 0.78, rows * 0.25
        blob = (((xx - ax) / 26) ** 2 + ((yy - ay) / 18) ** 2) <= 1
        canvas[blob] = 4000

    canvas = np.clip(canvas, 0, 4095)
    return canvas.astype(np.uint16), boxes


def make_dicom(scenario: Scenario, idx: int) -> tuple[FileDataset, list[dict]]:
    pixels, boxes = build_pixels(scenario)
    rows, cols = pixels.shape

    file_meta = FileMetaDataset()
    file_meta.MediaStorageSOPClassUID = pydicom.uid.SecondaryCaptureImageStorage
    file_meta.MediaStorageSOPInstanceUID = generate_uid()
    file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    file_meta.ImplementationClassUID = generate_uid()

    ds = FileDataset(f"{scenario.key}.dcm", {}, file_meta=file_meta, preamble=b"\x00" * 128)
    ds.is_little_endian = True
    ds.is_implicit_VR = False

    ds.SpecificCharacterSet = "ISO_IR 192"  # UTF-8, чтобы корректно хранить кириллицу
    ds.SOPClassUID = file_meta.MediaStorageSOPClassUID
    ds.SOPInstanceUID = file_meta.MediaStorageSOPInstanceUID
    ds.StudyInstanceUID = generate_uid()
    ds.SeriesInstanceUID = generate_uid()

    if scenario.build_kwargs.get("leave_phi"):
        # Намеренно "забытые" персональные данные — чтобы проверить, что backend
        # это обнаруживает и помечает исследование как непрошедшее обезличивание.
        ds.PatientName = "Testov^Ivan^Ivanovich"
        ds.PatientBirthDate = "19800101"
        ds.PatientID = "REAL-MRN-000123"
    else:
        ds.PatientName = "ANONYMIZED"
        ds.PatientID = f"ANON-{idx:04d}"
    ds.PatientSex = RNG.choice(["M", "F"])
    ds.PatientAge = f"{RNG.integers(35, 82):03d}Y"

    ds.StudyDate = "20260901"
    ds.StudyTime = "101500"
    ds.AccessionNumber = f"ACC{idx:06d}"
    ds.Modality = "DX"
    ds.BodyPartExamined = scenario.body_part
    ds.StudyDescription = scenario.title_ru
    ds.SeriesDescription = scenario.title_ru
    ds.Manufacturer = "SyntheticDXA-Generator"
    ds.SoftwareVersions = "synthetic-1.0"

    if not scenario.build_kwargs.get("strip_tags"):
        ds.PixelSpacing = [0.5, 0.5]
        ds.KVP = "80"
        ds.ImagerPixelSpacing = [0.5, 0.5]

    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.Rows, ds.Columns = rows, cols
    ds.BitsAllocated = 16
    ds.BitsStored = 12
    ds.HighBit = 11
    ds.PixelRepresentation = 0
    ds.WindowCenter = int(pixels.mean())
    ds.WindowWidth = 2200
    ds.RescaleIntercept = 0
    ds.RescaleSlope = 1
    ds.PixelData = pixels.tobytes()

    if scenario.build_kwargs.get("strip_tags"):
        # имитация технически неполного экспорта: убираем обязательные теги
        del ds.StudyInstanceUID
        del ds.SOPInstanceUID
        del ds.PhotometricInterpretation

    return ds, boxes


def main():
    if os.path.exists(OUT_DIR):
        shutil.rmtree(OUT_DIR)
    os.makedirs(OUT_DIR, exist_ok=True)
    os.makedirs(REAL_DIR, exist_ok=True)

    manifest = []
    for idx, sc in enumerate(SCENARIOS, start=1):
        ds, boxes = make_dicom(sc, idx)
        fname = f"{idx:02d}_{sc.key}.dcm"
        ds.save_as(os.path.join(OUT_DIR, fname))
        manifest.append({
            "file": fname,
            "title": sc.title_ru,
            "body_part": sc.body_part,
            "expected_verdict": sc.expected_verdict,
            "expected_category": sc.expected_category,
            "ground_truth_boxes": boxes,
        })

    with open(os.path.join(OUT_DIR, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    # ZIP-архив для проверки загрузки архивом исследований
    zip_path = os.path.join(OUT_DIR, "studies_bundle.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for item in manifest:
            zf.write(os.path.join(OUT_DIR, item["file"]), arcname=item["file"])

    # копируем пару настоящих DICOM из pydicom test data (другой модальности) —
    # чтобы протестировать устойчивость парсера к "чужим" реальным файлам
    from pydicom.data import get_testdata_file
    for real_name in ("CT_small.dcm", "MR_small.dcm"):
        src = get_testdata_file(real_name)
        if src:
            shutil.copy(src, os.path.join(REAL_DIR, real_name))

    print(f"Сгенерировано {len(manifest)} синтетических исследований в {OUT_DIR}")
    print(f"ZIP-бандл: {zip_path}")
    print(f"Реальные сторонние DICOM для теста парсера: {REAL_DIR}")


if __name__ == "__main__":
    main()
