"""
Обученные (numpy-only) скореры для снимков позвоночника — доработка шагов 4/5
плана переделки (`docs/ПЛАН_ПЕРЕДЕЛКИ.md`, `docs/СТАТУС_ПРОГРЕССА.md`).

Зачем. Классические эвристики `heuristics.py` на итоговых метриках ТЗ оказались
на уровне случайности (ROC-AUC quality_prob 0.495 [0.38, 0.60] на всей выборке;
для позвоночника укладка — F1 = 0.00, артефакты — F1 = 0.08, см.
`docs/model_vs_heuristic_ci.json` и `docs/СТАТУС_ПРОГРЕССА.md`). Причина в том,
что они проверяли не то, что просит ТЗ: горизонтальное смещение центра вместо
видимости гребней подвздошных костей внизу кадра (критерий укладки, ТЗ п.2.3,
рис.1), «лишние яркие компоненты» вместо признаков инородных предметов у
верхних углов кадра (рис.3).

Что здесь. Три небольшие логистические регрессии на четырёх признаках формы/
яркости (физически мотивированных критериями ТЗ), обученные на экспертной
разметке (`fit_spine_scorers.py`):
  - pos: вероятность нарушения укладки  (яркость нижних углов кадра);
  - art: вероятность посторонних предметов (ширина верхней полосы, яркость
         верхних углов);
  - any: вероятность «есть хоть одно нарушение» -> quality_prob для позвоночника.
Наклон оси остаётся физическим порогом ТЗ (5°, `heuristics.py`).

Ограничения (честно):
  - обучено на 193 снимках/99 исследованиях, 12/38 позитивов на укладку/
    артефакты — интервалы неопределённости широкие, см. docs/;
  - домен — ROI-кропы этого сканера (стороны ~235-320 px): вне диапазона
    `in_domain()` скореры НЕ применяются (heuristics.py остаётся на прежней
    логике), потому что признаки — яркость в фиксированных долях кадра — за
    пределами такого домена не имеют смысла (синтетические тесты, другой
    аппарат/кроп).
"""
from __future__ import annotations

import json
import pathlib

import numpy as np

WEIGHTS_PATH = pathlib.Path(__file__).parent / "weights" / "spine_scorers.json"

MIN_SIDE_PX = 150
MAX_SIDE_PX = 400

_cache: dict | None = None


def in_domain(rows: int, cols: int) -> bool:
    return MIN_SIDE_PX <= rows <= MAX_SIDE_PX and MIN_SIDE_PX <= cols <= MAX_SIDE_PX


def spine_features(img8: np.ndarray, mask: np.ndarray) -> dict[str, float]:
    """Признаки для скореров. Одна реализация и для обучения, и для инференса —
    расхождение здесь молча ломает модель, поэтому функция единственная."""
    rows, cols = img8.shape
    h4, w4 = int(rows * 0.2), int(cols * 0.25)
    c_bl = float(img8[-h4:, :w4].mean()) / 255
    c_br = float(img8[-h4:, -w4:].mean()) / 255
    c_tl = float(img8[:h4, :w4].mean()) / 255
    c_tr = float(img8[:h4, -w4:].mean()) / 255

    top_rows = int(np.linspace(0, rows, 11).astype(int)[1])
    band = mask[:top_rows]
    has = band.any(axis=1)
    first = band.argmax(axis=1)
    last = cols - 1 - band[:, ::-1].argmax(axis=1)
    widths = np.where(has, last - first + 1, 0)
    bw0 = float(widths.mean()) / cols if len(widths) else 0.0

    return {
        "c_bot_mean": (c_bl + c_br) / 2,
        "c_bottom_min": min(c_bl, c_br),
        "c_top_mean": (c_tl + c_tr) / 2,
        "bw0": bw0,
    }


def _load() -> dict | None:
    global _cache
    if _cache is None:
        if not WEIGHTS_PATH.exists():
            return None
        _cache = json.loads(WEIGHTS_PATH.read_text(encoding="utf-8"))
    return _cache


def available() -> bool:
    return _load() is not None


def score_spine(features: dict[str, float]) -> dict[str, dict]:
    """{'pos'|'art'|'any': {'p': вероятность, 'flag': bool | None}} — flag есть
    только у pos/art (у any это чистая вероятность для quality_prob)."""
    spec = _load()
    if spec is None:
        raise RuntimeError(f"нет файла весов {WEIGHTS_PATH} — запустите fit_spine_scorers")
    out: dict[str, dict] = {}
    for name, s in spec["scorers"].items():
        x = np.array([features[f] for f in s["features"]], dtype=np.float64)
        z = float(((x - np.array(s["mu"])) / np.array(s["sd"])) @ np.array(s["w"][:-1]) + s["w"][-1])
        thr = s.get("threshold_logit")
        out[name] = {"p": float(1.0 / (1.0 + np.exp(-z))), "flag": (z >= thr) if thr is not None else None}
    return out
