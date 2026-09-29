"""
Обучение и валидация CNN-модели (шаг 4 плана переделки) + честное сравнение
с эвристиками (`heuristics.py`) на тех же данных и тех же метках.

Кросс-валидация по ИССЛЕДОВАНИЯМ (GroupKFold), не по снимкам — иначе снимки
одного пациента попадут и в train, и в val (см. принцип шага 4 плана).
Метрика — ROC-AUC на объединённых out-of-fold предсказаниях (соответствует
`quality_prob` из шага 5: `quality_class` бинарный, а вероятность нужна для
ROC-AUC — см. «Разъяснения по вопросам ЛЦТ_V2.docx», вопрос 8).

Итог по каждому полю: чья метрика лучше — модели или эвристики. Модель
подключается к `interface.analyze_study` только если выигрывает; иначе
остаётся эвристика (явно разрешено планом — «недообученная сеть» хуже
работающей эвристики).
"""
from __future__ import annotations

import json
import random
import warnings

import cv2
import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, Dataset

from ai_module.calibration import get_pixel_spacing_mm
from ai_module.dicom_io import auto_windowed_uint8, get_pixel_array, read_dicom
from ai_module.heuristics import _detect_artifacts, _segment_main_object, _tilt_from_vertical_deg
from ai_module.model import IMAGE_SIZE, ViolationModel, crop_and_resize, to_tensor
from ai_module.model_data import HIP_FIELDS, SPINE_FIELDS, ImageExample, build_examples

warnings.filterwarnings("ignore")

SEED = 42
N_FOLDS = 5
EPOCHS = 12
BATCH_SIZE = 16
LR = 1e-4


def roc_auc(y_true: np.ndarray, y_score: np.ndarray) -> float | None:
    """Точный ROC-AUC через статистику Манна-Уитни (без sklearn)."""
    y_true = np.asarray(y_true, dtype=bool)
    y_score = np.asarray(y_score, dtype=float)
    pos = y_score[y_true]
    neg = y_score[~y_true]
    if len(pos) == 0 or len(neg) == 0:
        return None
    greater = (pos[:, None] > neg[None, :]).sum()
    ties = (pos[:, None] == neg[None, :]).sum()
    return float((greater + 0.5 * ties) / (len(pos) * len(neg)))


def group_kfold(studies: list[str], k: int, seed: int) -> list[tuple[set[str], set[str]]]:
    rng = random.Random(seed)
    shuffled = studies[:]
    rng.shuffle(shuffled)
    folds = [shuffled[i::k] for i in range(k)]
    splits = []
    for i in range(k):
        val = set(folds[i])
        train = set(shuffled) - val
        splits.append((train, val))
    return splits


class ExampleDataset(Dataset):
    def __init__(self, examples: list[ImageExample], augment: bool):
        self.examples = examples
        self.augment = augment

    def __len__(self) -> int:
        return len(self.examples)

    def __getitem__(self, idx: int):
        ex = self.examples[idx]
        ds = read_dicom(ex.path)
        arr = get_pixel_array(ds)
        img8 = auto_windowed_uint8(arr)
        crop = crop_and_resize(img8, ex.bbox)

        if self.augment:
            if random.random() < 0.5:
                crop = crop[:, ::-1].copy()  # горизонтальный флип — не портит метки (шаг 0/4, labels.py)
            angle = random.uniform(-6, 6)
            m = cv2.getRotationMatrix2D((IMAGE_SIZE / 2, IMAGE_SIZE / 2), angle, 1.0)
            crop = cv2.warpAffine(crop, m, (IMAGE_SIZE, IMAGE_SIZE), borderMode=cv2.BORDER_REPLICATE)
            gain = random.uniform(0.85, 1.15)
            bias = random.uniform(-15, 15)
            crop = np.clip(crop.astype(np.float32) * gain + bias, 0, 255).astype(np.uint8)

        tensor = to_tensor(crop)

        spine_target = np.zeros(len(SPINE_FIELDS), dtype=np.float32)
        spine_mask = np.zeros(len(SPINE_FIELDS), dtype=np.float32)
        hip_target = np.zeros(len(HIP_FIELDS), dtype=np.float32)
        hip_mask = np.zeros(len(HIP_FIELDS), dtype=np.float32)

        if ex.region == "spine":
            for i, f in enumerate(SPINE_FIELDS):
                if f in ex.labels:
                    spine_target[i] = float(ex.labels[f])
                    spine_mask[i] = 1.0
        else:
            for i, f in enumerate(HIP_FIELDS):
                if f in ex.labels:
                    hip_target[i] = float(ex.labels[f])
                    hip_mask[i] = 1.0

        return tensor, spine_target, spine_mask, hip_target, hip_mask, idx


def masked_bce(logits: torch.Tensor, target: torch.Tensor, mask: torch.Tensor, pos_weight: torch.Tensor) -> torch.Tensor:
    loss = nn.functional.binary_cross_entropy_with_logits(
        logits, target, weight=None, pos_weight=pos_weight, reduction="none"
    )
    loss = loss * mask
    denom = mask.sum()
    return loss.sum() / denom if denom > 0 else torch.tensor(0.0)


def compute_pos_weight(examples: list[ImageExample], region: str, fields: tuple[str, ...]) -> torch.Tensor:
    weights = []
    for f in fields:
        vals = [e.labels[f] for e in examples if e.region == region and f in e.labels]
        pos = sum(vals)
        neg = len(vals) - pos
        weights.append((neg / pos) if pos > 0 else 1.0)
    return torch.tensor(weights, dtype=torch.float32)


def train_one_fold(train_examples: list[ImageExample], val_examples: list[ImageExample]) -> dict[int, tuple[np.ndarray, np.ndarray]]:
    model = ViolationModel()
    model.train()
    optimizer = torch.optim.Adam(model.trainable_parameters(), lr=LR)

    spine_pos_weight = compute_pos_weight(train_examples, "spine", SPINE_FIELDS)
    hip_pos_weight = compute_pos_weight(train_examples, "hip", HIP_FIELDS)

    train_loader = DataLoader(ExampleDataset(train_examples, augment=True), batch_size=BATCH_SIZE, shuffle=True)

    for epoch in range(EPOCHS):
        for tensor, spine_t, spine_m, hip_t, hip_m, _ in train_loader:
            optimizer.zero_grad()
            spine_logits, hip_logits = model(tensor)
            loss = (
                masked_bce(spine_logits, spine_t, spine_m, spine_pos_weight)
                + masked_bce(hip_logits, hip_t, hip_m, hip_pos_weight)
            )
            loss.backward()
            optimizer.step()

    model.eval()
    val_loader = DataLoader(ExampleDataset(val_examples, augment=False), batch_size=BATCH_SIZE, shuffle=False)
    spine_probs, hip_probs = [], []
    with torch.no_grad():
        for tensor, *_ in val_loader:
            spine_logits, hip_logits = model(tensor)
            spine_probs.append(torch.sigmoid(spine_logits).numpy())
            hip_probs.append(torch.sigmoid(hip_logits).numpy())
    return np.concatenate(spine_probs, axis=0), np.concatenate(hip_probs, axis=0)


def heuristic_score(example: ImageExample, field: str) -> float:
    """Непрерывный эвристический скор для того же поля (для честного ROC-AUC
    сравнения — не бинарный finding, а сырая величина под ним)."""
    ds = read_dicom(example.path)
    arr = get_pixel_array(ds)
    img8 = auto_windowed_uint8(arr)
    rows, cols = img8.shape
    mask, bbox = _segment_main_object(img8)
    if bbox is None:
        return 0.0
    offset_frac = abs(bbox["cx"] - cols / 2) / cols

    if field == "incorrect_positioning":
        return offset_frac
    if field == "axis_misaligned":
        spacing = get_pixel_spacing_mm(ds)
        return _tilt_from_vertical_deg(mask, spacing)
    if field == "artifact":
        arts = _detect_artifacts(img8, mask)
        return float(sum(a["area"] for a in arts))
    if field == "incomplete_field_of_view":
        return -float(min(bbox["x0"], cols - 1 - bbox["x1"]))
    raise ValueError(field)


def main() -> None:
    import sys

    xlsx = sys.argv[1] if len(sys.argv) > 1 else "разметка.xlsx"
    studies_dir = sys.argv[2] if len(sys.argv) > 2 else "Исследования"

    examples = build_examples(xlsx, studies_dir)
    studies = sorted({e.study for e in examples})
    splits = group_kfold(studies, N_FOLDS, SEED)

    oof_spine_prob = np.zeros((len(examples), len(SPINE_FIELDS)))
    oof_hip_prob = np.zeros((len(examples), len(HIP_FIELDS)))
    idx_by_study: dict[str, list[int]] = {}
    for i, e in enumerate(examples):
        idx_by_study.setdefault(e.study, []).append(i)

    for fold_i, (train_studies, val_studies) in enumerate(splits):
        train_idx = [i for s in train_studies for i in idx_by_study.get(s, [])]
        val_idx = [i for s in val_studies for i in idx_by_study.get(s, [])]
        train_examples = [examples[i] for i in train_idx]
        val_examples = [examples[i] for i in val_idx]
        if not val_examples:
            continue
        print(f"fold {fold_i}: train={len(train_examples)} val={len(val_examples)}", flush=True)
        spine_probs, hip_probs = train_one_fold(train_examples, val_examples)
        for local_i, global_i in enumerate(val_idx):
            oof_spine_prob[global_i] = spine_probs[local_i]
            oof_hip_prob[global_i] = hip_probs[local_i]

    report: dict[str, dict] = {}
    raw: dict[str, dict] = {}  # для bootstrap-ДИ (bootstrap_ci.py) — сырые массивы по study
    for region, fields, oof_prob in (("spine", SPINE_FIELDS, oof_spine_prob), ("hip", HIP_FIELDS, oof_hip_prob)):
        region_examples = [(i, e) for i, e in enumerate(examples) if e.region == region]
        for fi, f in enumerate(fields):
            idx = [i for i, e in region_examples if f in e.labels]
            if not idx:
                continue
            y_true = np.array([examples[i].labels[f] for i in idx])
            model_scores = oof_prob[idx, fi]
            heur_scores = np.array([heuristic_score(examples[i], f) for i in idx])
            model_auc = roc_auc(y_true, model_scores)
            heur_auc = roc_auc(y_true, heur_scores)
            report[f"{region}.{f}"] = {
                "n": len(idx), "n_positive": int(y_true.sum()),
                "model_auc": model_auc, "heuristic_auc": heur_auc,
            }
            raw[f"{region}.{f}"] = {
                "study": [examples[i].study for i in idx],
                "y_true": y_true.astype(int).tolist(),
                "model_score": model_scores.tolist(),
                "heuristic_score": heur_scores.tolist(),
            }
            print(f"{region}.{f}: n={len(idx)} pos={int(y_true.sum())} "
                  f"model_auc={model_auc} heuristic_auc={heur_auc}", flush=True)

    with open("../_extract/model_vs_heuristic_report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    with open("../_extract/model_vs_heuristic_raw.json", "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False)


if __name__ == "__main__":
    torch.manual_seed(SEED)
    random.seed(SEED)
    main()
