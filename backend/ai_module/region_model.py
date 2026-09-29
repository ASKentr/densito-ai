"""
Определение анатомической области (позвоночник / бедро) по уменьшенному
снимку — логистическая регрессия на пикселях миниатюры 32×32.

Почему не только симметрия (`heuristics.classify_anatomical_region`): проверка
на всех 499 снимках обучающего набора показала, что 27 снимков бедра (таз
целиком, эндопротез, широкий захват) дают симметрию 0.31–0.51 и уходили в
«позвоночник», а минимальная симметрия у позвоночника — 0.43: по одному этому
признаку классы не разделяются. Миниатюра сохраняет форму целиком (вертикальный
столб позвонков против головки и диафиза бедра), чего одной симметрии мало.

Веса — `weights/region_classifier.json`, обучение и кросс-валидация —
`fit_region_classifier.py`, разметка области — `region_labels.csv` (каждый
снимок набора просмотрен глазами). Если файла весов нет, вызывающий код
возвращается к правилу симметрии.
"""

from __future__ import annotations

import json
import pathlib

import cv2
import numpy as np

WEIGHTS_PATH = pathlib.Path(__file__).parent / "weights" / "region_classifier.json"
THUMB = 32

_cache: dict | None = None


def region_features(img8: np.ndarray) -> np.ndarray:
    """Миниатюра THUMB×THUMB (без сохранения пропорций), значения 0..1."""
    thumb = cv2.resize(img8, (THUMB, THUMB), interpolation=cv2.INTER_AREA)
    return thumb.astype(np.float64).ravel() / 255.0


def _load() -> dict | None:
    global _cache
    if _cache is None:
        if not WEIGHTS_PATH.exists():
            return None
        _cache = json.loads(WEIGHTS_PATH.read_text(encoding="utf-8"))
    return _cache


def available() -> bool:
    return _load() is not None


def spine_probability(img8: np.ndarray) -> float:
    """Вероятность того, что на снимке поясничный отдел позвоночника."""
    spec = _load()
    if spec is None:
        raise RuntimeError(f"нет файла весов {WEIGHTS_PATH} — запустите fit_region_classifier")
    x = (region_features(img8) - np.array(spec["mu"])) / np.array(spec["sd"])
    z = float(x @ np.array(spec["w"]) + spec["b"])
    return float(1.0 / (1.0 + np.exp(-z)))
