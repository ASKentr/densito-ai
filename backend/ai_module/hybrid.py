"""
Гибридная реализация `analyze_study` (шаг 4 плана переделки) — не полная
замена эвристик, а точечная: для каждого поля берётся то, что подтверждено
95%-бутстрэп-ДИ по исследованиям (`bootstrap_ci.py`, отчёт
`docs/model_vs_heuristic_ci.json`).

  - hip.incorrect_positioning -> CNN (`model.py`): ДИ модели [0.742, 0.932]
    не пересекается с ДИ эвристики [0.390, 0.687] — единственное поле с
    убедительной, статистически надёжной разницей.
  - spine.incorrect_positioning -> ЭВРИСТИКА (не CNN!). Раньше (до фикса
    классификатора региона) точечная оценка модели выглядела лучше
    (0.670 vs 0.516), но её ДИ [0.321, 0.810] и ДИ эвристики [0.249, 0.642]
    перекрываются почти полностью — разница статистически не подтверждена,
    прежнее решение было артефактом шума на маленькой выборке (12 позитивов).
  - spine_axis_misaligned, spine.artifact, incomplete_field_of_view ->
    эвристики: либо ДИ эвристики выше и не пересекается с моделью
    (axis_misaligned: 0.629-0.881 vs 0.345-0.677), либо оба ДИ пересекают
    0.5 и не различимы (artifact, incomplete_field_of_view — для последнего
    вообще всего 1 позитивный пример на весь датасет).

`heuristics.py` НЕ изменяется и не удаляется — используется как основа,
результат этого модуля совпадает с эвристикой везде, кроме укладки бедра.
`interface.py` не знает о существовании этого модуля: чтобы включить его
вместо чистых эвристик, backend должен импортировать `analyze_study` отсюда,
а не из `heuristics.py` — сознательное решение, а не значение по умолчанию.
"""
from __future__ import annotations

import pathlib

import numpy as np
import torch

from ai_module import heuristics
from ai_module.dicom_io import auto_windowed_uint8, get_pixel_array, read_dicom
from ai_module.heuristics import SEVERITY_PENALTY, _segment_main_object
from ai_module.interface import AIFinding, AIImageReport, AIStudyResult
from ai_module.model import ViolationModel, crop_and_resize, to_tensor
from ai_module.model_data import HIP_FIELDS, SPINE_FIELDS

WEIGHTS_PATH = pathlib.Path(__file__).parent / "weights" / "violation_model.pt"
# Порог флага «Некорректная укладка» бедра. ТЗ порога не задаёт — это наш
# параметр. Правило (то же, что у скореров позвоночника, fit_spine_scorers.py):
# помечать такую долю снимков, какова доля нарушений в разметке —
# квантиль OOF-вероятностей CNN (model_vs_heuristic_raw.json) уровня
# 1 - 47/261. Выбрано правило, а не число, подобранное под F1; прежний порог
# 0.5 давал чувствительность 0.32 (ТЗ п.8.4 ставит её первой, п.8.1 — работа
# с дисбалансом классов). Честная оценка с порогом, посчитанным без данных
# проверочного фолда, — ai_module/eval_pipeline_cv.py, docs/КАЧЕСТВО_РЕШЕНИЯ.md.
# При переобучении CNN пересчитать: eval_pipeline_cv.py печатает значение.
POSITIONING_THRESHOLD = 0.2735
MODEL_VERSION = "hybrid-cnn-hip-positioning-0.2"

# Только для этой пары (регион, поле) CNN статистически надёжно лучше
# эвристики (см. docstring модуля / docs/model_vs_heuristic_ci.json).
CNN_OVERRIDE_REGIONS = {"hip"}

_model: ViolationModel | None = None


def _get_model() -> ViolationModel:
    global _model
    if _model is None:
        m = ViolationModel(pretrained=False)
        m.load_state_dict(torch.load(WEIGHTS_PATH, map_location="cpu"))
        m.eval()
        _model = m
    return _model


def _positioning_probability(path: str, region: str) -> float | None:
    ds = read_dicom(path)
    arr = get_pixel_array(ds)
    img8 = auto_windowed_uint8(arr)
    mask, bbox = _segment_main_object(img8)
    if bbox is None:
        return None
    crop = crop_and_resize(img8, (bbox["y0"], bbox["y1"], bbox["x0"], bbox["x1"]))
    tensor = to_tensor(crop).unsqueeze(0)
    with torch.no_grad():
        spine_logits, hip_logits = _get_model()(tensor)
    if region == "spine":
        idx = SPINE_FIELDS.index("incorrect_positioning")
        prob = torch.sigmoid(spine_logits[0, idx]).item()
    else:
        idx = HIP_FIELDS.index("incorrect_positioning")
        prob = torch.sigmoid(hip_logits[0, idx]).item()
    return float(prob)


def analyze_image(image_index: int, path: str) -> tuple[AIImageReport, list[AIFinding]]:
    report, findings = heuristics.analyze_image(image_index, path)
    region = report.anatomical_region
    if region in CNN_OVERRIDE_REGIONS:
        prob = _positioning_probability(path, region)
        if prob is not None:
            findings = [f for f in findings if f.violation_code != "incorrect_positioning"]
            if prob >= POSITIONING_THRESHOLD:
                findings.append(AIFinding(
                    image_index=image_index, violation_code="incorrect_positioning",
                    severity="high", confidence=prob, bbox=None,
                    explanation=f"CNN-модель (укладка): вероятность нарушения {prob:.2f} "
                                f"(порог {POSITIONING_THRESHOLD}).",
                ))
            report.metrics["cnn_positioning_prob"] = prob

    penalty = sum(SEVERITY_PENALTY.get(f.severity, 5) for f in findings)
    report.quality_score = float(np.clip(100 - penalty, 0, 100))
    return report, findings


def analyze_study(image_paths: list[str]) -> AIStudyResult:
    all_findings: list[AIFinding] = []
    image_reports: list[AIImageReport] = []

    for idx, path in enumerate(image_paths):
        report, findings = analyze_image(idx, path)
        image_reports.append(report)
        all_findings.extend(findings)

    overall_score = float(np.mean([r.quality_score for r in image_reports])) if image_reports else 0.0
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
