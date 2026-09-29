"""
Обучение определителя анатомической области (`region_model.py`) и его
кросс-валидация по исследованиям (снимки одного исследования — в одном фолде).

    cd backend
    python -m ai_module.fit_region_classifier "../_extract/НД_для_обучения/Исследования" \
        "../_extract/Для теста/Для теста"

Разметка — `ai_module/region_labels.csv` (путь снимка относительно папки
исследований, spine|hip): все 499 снимков просмотрены глазами. Второй аргумент
(необязательный) — папка с тестовыми снимками организатора: на обучении они не
используются, только для проверки (область — по суффиксу имени: ПОП —
позвоночник, ЛПОБ/ППОБ — бедро).

Пишет `ai_module/weights/region_classifier.json` и `../docs/region_classifier_cv.json`.

Решение в работе: симметрия ниже порога — бедро (у позвоночника в наборе
минимум 0.43, ни одной ошибки этого правила), иначе — модель. Это объединение
выбрано ПОСЛЕ просмотра CV модели (её 3 ошибки: 2 бедра с эндопротезом и
1 позвоночник, правило симметрии на них право) — отчёт даёт обе точности.

Гиперпараметры модели заданы до просмотра результатов: миниатюра 32×32, L2 = 0.1 на
среднем лоссе, аугментация — зеркальное отражение по горизонтали (левое и
правое бедро; позвоночник при отражении остаётся позвоночником).
"""

from __future__ import annotations

import csv
import json
import pathlib
import sys

import numpy as np
from scipy.optimize import minimize

from ai_module.dicom_io import auto_windowed_uint8, get_pixel_array, read_dicom
from ai_module.heuristics import REGION_SYMMETRY_THRESHOLD, _segment_main_object, _symmetry
from ai_module.region_model import THUMB, WEIGHTS_PATH, region_features

LABELS_PATH = pathlib.Path(__file__).parent / "region_labels.csv"
CV_REPORT = pathlib.Path(__file__).resolve().parents[2] / "docs" / "region_classifier_cv.json"
L2 = 0.1
FOLDS = 5
SEED = 0


def _image(path: pathlib.Path) -> np.ndarray:
    return auto_windowed_uint8(get_pixel_array(read_dicom(str(path))))


def _symmetry_of(img8: np.ndarray) -> float:
    mask, bbox = _segment_main_object(img8)
    return _symmetry(mask) if bbox is not None else 0.0


def _fit(x: np.ndarray, y: np.ndarray) -> dict:
    mu, sd = x.mean(axis=0), x.std(axis=0) + 1e-6
    xs = (x - mu) / sd
    n, d = xs.shape

    def loss(wb):
        w, b = wb[:-1], wb[-1]
        z = xs @ w + b
        p = 1.0 / (1.0 + np.exp(-z))
        val = np.mean(np.logaddexp(0.0, z) - y * z) + 0.5 * L2 * w @ w
        g = p - y
        return val, np.append(xs.T @ g / n + L2 * w, g.mean())

    res = minimize(loss, np.zeros(d + 1), jac=True, method="L-BFGS-B", options={"maxiter": 2000})
    return {"mu": mu.tolist(), "sd": sd.tolist(), "w": res.x[:-1].tolist(), "b": float(res.x[-1])}


def _predict(spec: dict, x: np.ndarray) -> np.ndarray:
    z = ((x - np.array(spec["mu"])) / np.array(spec["sd"])) @ np.array(spec["w"]) + spec["b"]
    return 1.0 / (1.0 + np.exp(-z))


def _augment(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    flipped = x.reshape(-1, THUMB, THUMB)[:, :, ::-1].reshape(len(x), -1)
    return np.vstack([x, flipped]), np.concatenate([y, y])


def main() -> None:
    studies_dir = pathlib.Path(sys.argv[1])
    test_dir = pathlib.Path(sys.argv[2]) if len(sys.argv) > 2 else None

    with open(LABELS_PATH, encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    feats, ys, studies, sym = [], [], [], []
    for r in rows:
        img8 = _image(studies_dir / r["path"])
        feats.append(region_features(img8))
        ys.append(1.0 if r["region"] == "spine" else 0.0)
        studies.append(r["path"].split("/")[0])
        sym.append(_symmetry_of(img8))
    x, y = np.array(feats), np.array(ys)

    uniq = sorted(set(studies))
    rng = np.random.default_rng(SEED)
    rng.shuffle(uniq)
    fold_of = {s: i % FOLDS for i, s in enumerate(uniq)}
    folds = np.array([fold_of[s] for s in studies])
    oof = np.zeros(len(y))
    for k in range(FOLDS):
        tr = folds != k
        spec = _fit(*_augment(x[tr], y[tr]))
        oof[~tr] = _predict(spec, x[~tr])
    sym = np.array(sym)
    sym_pred = (sym >= REGION_SYMMETRY_THRESHOLD).astype(float)
    model_pred = (oof >= 0.5).astype(float)
    # боевое правило (heuristics.classify_anatomical_region): низкая симметрия —
    # бедро, в пограничной и высокой зоне решает модель
    pred = np.where(sym_pred == 0, 0.0, model_pred)
    errors = [rows[i]["path"] for i in np.where(pred != y)[0]]

    final = _fit(*_augment(x, y))
    final.update({"thumb": THUMB, "l2": L2, "n_train": int(len(y)),
                  "n_spine": int(y.sum()), "n_hip": int(len(y) - y.sum())})
    WEIGHTS_PATH.write_text(json.dumps(final), encoding="utf-8")

    report = {
        "n_images": int(len(y)), "n_studies": len(uniq), "folds": FOLDS, "seed": SEED,
        "n_spine": int(y.sum()), "n_hip": int(len(y) - y.sum()),
        "decision": f"symmetry < {REGION_SYMMETRY_THRESHOLD} -> hip, otherwise model",
        "cv_accuracy": float((pred == y).mean()), "cv_errors": errors,
        "cv_model_only_accuracy": float((model_pred == y).mean()),
        "min_spine_symmetry": float(sym[y == 1].min()),
        "symmetry_rule_accuracy": float((sym_pred == y).mean()),
        "symmetry_rule_errors": int((sym_pred != y).sum()),
    }
    if test_dir and test_dir.is_dir():
        test = {}
        for f in sorted(test_dir.glob("*.dcm")):
            truth = "spine" if "ПОП" in f.stem else "hip"
            img8 = _image(f)
            p = float(_predict(final, region_features(img8)[None, :])[0])
            region = "spine" if _symmetry_of(img8) >= REGION_SYMMETRY_THRESHOLD and p >= 0.5 else "hip"
            test[f.name] = {"truth": truth, "spine_probability": round(p, 4),
                            "correct": region == truth}
        report["organizer_test"] = test
    CV_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items()}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
