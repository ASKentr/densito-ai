"""
Обучение скореров позвоночника (`learned_scorers.py`) на экспертной разметке и
воспроизводимая оценка кросс-валидацией по ИСследованиям (не по снимкам).

    cd backend
    python -m ai_module.fit_spine_scorers "../_extract/НД_для_обучения/разметка.xlsx" \
        "../_extract/НД_для_обучения/Исследования"

Пишет `ai_module/weights/spine_scorers.json` (веса для инференса) и
`../docs/spine_scorers_cv.json` (метрики CV с бутстрэп-ДИ).

Гиперпараметры заданы ДО просмотра результатов и не подбирались под CV:
L2 = 0.01 на среднем лоссе (слабая регуляризация; при сильной — λ≥1 — наклоны
обнуляются, а объединённые OOF-предсказания портятся межфолдовым сдвигом
свободного члена — проверено), пороги флагов — квантиль обучающих оценок по
доле позитивов (prevalence matching), без подгонки под val.
"""
from __future__ import annotations

import json
import random
import sys
import warnings

import numpy as np
from scipy.optimize import minimize

from ai_module.calibration import get_pixel_spacing_mm
from ai_module.dicom_io import auto_windowed_uint8, get_pixel_array, read_dicom
from ai_module.heuristics import TILT_ANGLE_THRESHOLD_DEG, _segment_main_object, _tilt_from_vertical_deg
from ai_module.learned_scorers import WEIGHTS_PATH, spine_features
from ai_module.model_data import build_examples

LAM = 0.01
CV_REPEATS = 20
BOOTSTRAP = 500

SCORERS = {
    "pos": {"features": ["c_bot_mean", "c_bottom_min"], "target": "incorrect_positioning", "flag": True},
    "art": {"features": ["bw0", "c_top_mean"], "target": "artifact", "flag": True},
    "any": {"features": ["c_bot_mean", "bw0", "c_top_mean"], "target": None, "flag": False},
}


def _auc(y, s):
    y = np.asarray(y, bool)
    s = np.asarray(s, float)
    p, n = s[y], s[~y]
    if len(p) == 0 or len(n) == 0:
        return None
    return float(((p[:, None] > n[None, :]).sum() + 0.5 * (p[:, None] == n[None, :]).sum()) / (len(p) * len(n)))


def _f1(y, pred):
    tp = int(((pred == 1) & (y == 1)).sum())
    fp = int(((pred == 1) & (y == 0)).sum())
    fn = int(((pred == 0) & (y == 1)).sum())
    if tp == 0:
        return 0.0
    p, r = tp / (tp + fp), tp / (tp + fn)
    return 2 * p * r / (p + r)


def _fit(X, y, lam=LAM):
    mu, sd = X.mean(0), X.std(0) + 1e-9
    Z1 = np.hstack([(X - mu) / sd, np.ones((len(X), 1))])

    def loss(w):
        z = Z1 @ w
        return (np.logaddexp(0, z) - y * z).mean() + lam * np.sum(w[:-1] ** 2)

    def grad(w):
        g = Z1.T @ (1 / (1 + np.exp(-(Z1 @ w))) - y) / len(y)
        g[:-1] += 2 * lam * w[:-1]
        return g

    w0 = np.zeros(Z1.shape[1])
    w0[-1] = np.log(max(y.mean(), 1e-3) / (1 - y.mean() + 1e-9))
    return mu, sd, minimize(loss, w0, jac=grad, method="L-BFGS-B").x


def _logit(model, X):
    mu, sd, w = model
    return np.hstack([(X - mu) / sd, np.ones((len(X), 1))]) @ w


def collect(xlsx: str, studies_dir: str):
    warnings.filterwarnings("ignore")
    feats, ys, studies, tilts = [], [], [], []
    for ex in build_examples(xlsx, studies_dir):
        if ex.region != "spine" or not all(k in ex.labels for k in ("incorrect_positioning", "axis_misaligned", "artifact")):
            continue
        ds = read_dicom(ex.path)
        img8 = auto_windowed_uint8(get_pixel_array(ds))
        mask, bbox = _segment_main_object(img8)
        if bbox is None:
            continue
        feats.append(spine_features(img8, mask))
        tilts.append(_tilt_from_vertical_deg(mask, get_pixel_spacing_mm(ds)))
        ys.append({k: int(v) for k, v in ex.labels.items()})
        studies.append(ex.study)
    return feats, ys, np.array(studies), np.array(tilts)


def main() -> None:
    xlsx, studies_dir = sys.argv[1], sys.argv[2]
    feats, ys, studies, tilts = collect(xlsx, studies_dir)
    y_pos = np.array([y["incorrect_positioning"] for y in ys])
    y_axis = np.array([y["axis_misaligned"] for y in ys])
    y_art = np.array([y["artifact"] for y in ys])
    axis_flag = (tilts > TILT_ANGLE_THRESHOLD_DEG).astype(int)
    y_any = np.array([int(any(y.values())) for y in ys])
    targets = {"pos": y_pos, "art": y_art, "any": y_any}
    X = {n: np.array([[f[k] for k in s["features"]] for f in feats]) for n, s in SCORERS.items()}
    us = sorted(set(studies))
    idx = {u: np.where(studies == u)[0] for u in us}

    # ---- финальное обучение на всех данных ----
    spec = {"lambda": LAM, "n_train": len(feats), "n_studies": len(us),
            "prevalence": {n: float(t.mean()) for n, t in targets.items()}, "scorers": {}}
    for name, s in SCORERS.items():
        mu, sd, w = _fit(X[name], targets[name])
        entry = {"features": s["features"], "mu": mu.tolist(), "sd": sd.tolist(), "w": w.tolist()}
        if s["flag"]:
            entry["threshold_logit"] = float(np.quantile(_logit((mu, sd, w), X[name]), 1 - targets[name].mean()))
        spec["scorers"][name] = entry
    WEIGHTS_PATH.parent.mkdir(exist_ok=True)
    WEIGHTS_PATH.write_text(json.dumps(spec, ensure_ascii=False, indent=1), encoding="utf-8")

    # ---- CV по исследованиям (повторная 5-fold) ----
    res = {n: {"auc": [], "f1": []} for n in SCORERS}
    class_f1, macro_f1 = [], []
    avg = {n: np.zeros(len(feats)) for n in SCORERS}
    for rep in range(CV_REPEATS):
        rng = random.Random(4242 + rep)
        sh = us[:]
        rng.shuffle(sh)
        oof = {n: np.zeros(len(feats)) for n in SCORERS}
        pred = {n: np.zeros(len(feats), int) for n in SCORERS}
        for i in range(5):
            val = set(sh[i::5])
            va = np.concatenate([idx[u] for u in val])
            tr = np.concatenate([idx[u] for u in us if u not in val])
            for n, s in SCORERS.items():
                m = _fit(X[n][tr], targets[n][tr])
                oof[n][va] = _logit(m, X[n][va])
                if s["flag"]:
                    thr = np.quantile(_logit(m, X[n][tr]), 1 - targets[n][tr].mean())
                    pred[n][va] = (oof[n][va] >= thr).astype(int)
        for n, s in SCORERS.items():
            res[n]["auc"].append(_auc(targets[n], oof[n]))
            if s["flag"]:
                res[n]["f1"].append(_f1(targets[n], pred[n]))
            avg[n] += oof[n]
        # итог как в боевом пайплайне: quality_class = любой флаг (укладка | артефакт | наклон > 5°)
        class_f1.append(_f1(y_any, ((pred["pos"] + pred["art"] + axis_flag) > 0).astype(int)))
        macro_f1.append(np.mean([_f1(y_pos, pred["pos"]), _f1(y_axis, axis_flag), _f1(y_art, pred["art"])]))

    report = {
        "n": len(feats), "n_studies": len(us), "lambda": LAM, "cv_repeats": CV_REPEATS,
        "pipeline": {
            "quality_class_f1_cv": float(np.mean(class_f1)),
            "axis_f1_tilt_gt_5deg": float(_f1(y_axis, axis_flag)),
            "macro_f1_over_3_spine_types_cv": float(np.mean(macro_f1)),
            "prevalence_any": float(y_any.mean()),
        },
        "scorers": {},
    }
    print("pipeline", report["pipeline"], flush=True)
    for n in SCORERS:
        a = avg[n] / CV_REPEATS
        rng = random.Random(77)
        boots = []
        for _ in range(BOOTSTRAP):
            ii = np.concatenate([idx[rng.choice(us)] for _ in us])
            v = _auc(targets[n][ii], a[ii])
            if v is not None:
                boots.append(v)
        lo, hi = np.percentile(boots, [2.5, 97.5])
        report["scorers"][n] = {
            "n_positive": int(targets[n].sum()),
            "cv_auc_mean": float(np.mean(res[n]["auc"])), "cv_auc_bootstrap95": [float(lo), float(hi)],
            "cv_f1_mean": float(np.mean(res[n]["f1"])) if res[n]["f1"] else None,
        }
        print(n, report["scorers"][n], flush=True)
    with open("../docs/spine_scorers_cv.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
