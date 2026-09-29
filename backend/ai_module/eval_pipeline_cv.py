"""
Итоговые метрики ТЗ (п.8.4) для всего решения — честная кросс-валидация по
исследованиям, на уровне строк выходной таблицы (одна строка = один снимок).

    cd backend
    python -m ai_module.eval_pipeline_cv "../_extract/НД_для_обучения/разметка.xlsx" \
        "../_extract/НД_для_обучения/Исследования" "../_extract/model_vs_heuristic_raw.json"

Пишет `../docs/pipeline_cv.json`.

Как устроено. Прогоняется НАСТОЯЩИЙ боевой код (`ai_module.batch.process_file`
-> `hybrid`/`heuristics`), а обученные части подменяются моделями, не видевшими
данный снимок:
  - CNN укладки бедра — сохранённые out-of-fold вероятности из
    `model_train.py` (5-fold GroupKFold по исследованиям, seed 42);
  - скореры позвоночника (`learned_scorers.py`) — переобучаются на каждом из
    ТЕХ ЖЕ 5 разбиений тем же кодом, что `fit_spine_scorers.py`.
Правила без обучения (наклон 5°, область интереса бедра, классификатор
области) работают как есть — на них ничего не подгонялось по этой разметке,
кроме порога симметрии классификатора области (калибровался на части тех же
данных, см. heuristics.py; влияет на то, какие снимки к какой области отнесены).

Конфигурации:
  old          — эвристики до доработки (USE_LEARNED_SPINE_SCORERS=False), без обучения;
  heuristics   — эвристики + обученные скореры позвоночника (движок `heuristics`);
  hybrid_thr05 — то же + CNN для укладки бедра с прежним порогом 0.5;
  hybrid       — боевой движок `hybrid`: порог CNN по правилу «доля помеченных =
                 доля нарушений». В CV порог для фолда считается только по OOF и
                 меткам ОСТАЛЬНЫХ фолдов — данные проверочного фолда в него не входят.

Разметка на уровне исследования -> снимка (как в model_data.py): позвоночник —
если оценены все 3 поля; бедро — сторона снимка неизвестна, поле берётся, если
оценки правого и левого бедра совпадают (иначе снимок исключается из метрик,
где это поле нужно). «Нарушение» (quality_class/quality_prob) = хотя бы одно
официальное нарушение области.

Метрики — по ТЗ п.8.4, по областям, по типам нарушений и в целом:
quality_prob -> ROC-AUC, PR-AUC; quality_class -> F1, чувствительность,
специфичность, сбалансированная точность; violation_type -> те же бинарные
метрики по каждой из 5 пар (область, тип) и macro-F1. 95% ДИ — bootstrap по
исследованиям.
"""
from __future__ import annotations

import json
import random
import sys
import warnings
from pathlib import Path

import numpy as np

from ai_module import heuristics, hybrid, learned_scorers
from ai_module.dicom_io import auto_windowed_uint8, get_pixel_array, read_dicom
from ai_module.fit_spine_scorers import LAM, SCORERS, _auc, _f1, _fit, _logit
from ai_module.heuristics import _segment_main_object
from ai_module.model_data import HIP_FIELDS, SPINE_FIELDS, build_examples
from ai_module.model_train import N_FOLDS, SEED, group_kfold
from ai_module.batch import process_file
from ai_module.submission_mapping import map_violation_types

BOOTSTRAP = 2000

# (область, поле разметки) -> внутренний код нарушения (submission_mapping.py)
TYPES = [
    ("spine", "incorrect_positioning", "incorrect_positioning"),
    ("spine", "axis_misaligned", "spine_axis_misaligned"),
    ("spine", "artifact", "artifact"),
    ("hip", "incorrect_positioning", "incorrect_positioning"),
    ("hip", "incomplete_field_of_view", "incomplete_field_of_view"),
]


def _load_cnn_oof(raw_path: str, examples) -> dict[str, float]:
    """OOF-вероятности CNN (model_vs_heuristic_raw.json) -> {путь снимка: p}.
    В raw строки идут в порядке build_examples, отфильтрованные по наличию
    метки поля — проверяем совпадение исследований и меток, прежде чем
    доверять сопоставлению."""
    raw = json.load(open(raw_path, encoding="utf-8"))["hip.incorrect_positioning"]
    rows = [e for e in examples if e.region == "hip" and "incorrect_positioning" in e.labels]
    if len(rows) != len(raw["study"]):
        raise SystemExit(f"OOF не сопоставляются: {len(rows)} примеров против {len(raw['study'])} в raw")
    for e, s, y in zip(rows, raw["study"], raw["y_true"]):
        if e.study != s or int(e.labels["incorrect_positioning"]) != y:
            raise SystemExit("OOF не сопоставляются: порядок/метки расходятся — переобучите model_train.py")
    return {e.path: float(p) for e, p in zip(rows, raw["model_score"])}


def _cnn_threshold(cnn_oof: dict[str, float], examples, exclude_studies=frozenset()) -> float:
    """Правило hybrid.POSITIONING_THRESHOLD: квантиль OOF-вероятностей CNN на
    уровне 1 - доля нарушений укладки бедра (метки нужны только для доли)."""
    rows = [e for e in examples if e.path in cnn_oof and e.study not in exclude_studies]
    p = np.array([cnn_oof[e.path] for e in rows])
    prevalence = np.mean([e.labels["incorrect_positioning"] for e in rows])
    return float(np.quantile(p, 1 - prevalence))


def _spine_spec(feats: list[dict], ys: list[dict]) -> dict:
    """Та же процедура, что финальное обучение в fit_spine_scorers.main()."""
    y_any = np.array([int(any(y.values())) for y in ys])
    targets = {"pos": np.array([y["incorrect_positioning"] for y in ys]),
               "art": np.array([y["artifact"] for y in ys]), "any": y_any}
    spec = {"lambda": LAM, "scorers": {}}
    for name, s in SCORERS.items():
        X = np.array([[f[k] for k in s["features"]] for f in feats])
        mu, sd, w = _fit(X, targets[name])
        entry = {"features": s["features"], "mu": mu.tolist(), "sd": sd.tolist(), "w": w.tolist()}
        if s["flag"]:
            entry["threshold_logit"] = float(np.quantile(_logit((mu, sd, w), X), 1 - targets[name].mean()))
        spec["scorers"][name] = entry
    return spec


def _truth(e) -> dict:
    fields = SPINE_FIELDS if e.region == "spine" else HIP_FIELDS
    t = {f: int(e.labels[f]) for f in fields if f in e.labels}
    t["any"] = int(any(t.values())) if len(t) == len(fields) else None
    return t


def _pr_auc(y, score) -> float | None:
    """Average precision (площадь под PR-кривой, ступенчатая оценка)."""
    y = np.asarray(y, int)
    if y.sum() == 0:
        return None
    y = y[np.argsort(-np.asarray(score, float), kind="mergesort")]
    precision = np.cumsum(y) / np.arange(1, len(y) + 1)
    return float((precision * y).sum() / y.sum())


def _binary(y, pred) -> dict:
    y, pred = np.asarray(y, int), np.asarray(pred, int)
    tp = int(((pred == 1) & (y == 1)).sum())
    tn = int(((pred == 0) & (y == 0)).sum())
    fp = int(((pred == 1) & (y == 0)).sum())
    fn = int(((pred == 0) & (y == 1)).sum())
    sens = tp / (tp + fn) if tp + fn else None
    spec = tn / (tn + fp) if tn + fp else None
    return {
        "f1": _f1(y, pred), "sensitivity": sens, "specificity": spec,
        "precision": tp / (tp + fp) if tp + fp else None,
        "balanced_accuracy": (sens + spec) / 2 if sens is not None and spec is not None else None,
        "n": int(len(y)), "n_positive": int(y.sum()),
    }


def _metrics(rows: list[dict]) -> dict:
    any_rows = [r for r in rows if r["truth"]["any"] is not None and r["status"] == "Success"]
    y = np.array([r["truth"]["any"] for r in any_rows])
    score = [r["prob"] for r in any_rows]
    out = {"quality_prob_roc_auc": _auc(y, score), "quality_prob_pr_auc": _pr_auc(y, score)}
    out.update({f"quality_class_{k}": v for k, v in _binary(y, [r["cls"] for r in any_rows]).items()})
    per_type = {}
    for region, field, code in TYPES:
        tr = [r for r in rows if r["region"] == region and field in r["truth"] and r["status"] == "Success"]
        if not tr:
            continue
        official = map_violation_types(region, [code])  # дословная строка из таблицы
        per_type[f"{region}.{field}"] = _binary([r["truth"][field] for r in tr],
                                                [int(official in r["types"]) for r in tr])
    out["violation_type_macro_f1"] = float(np.mean([m["f1"] for m in per_type.values()]))
    out["per_type"] = per_type
    return out


def _scalars(m: dict) -> dict[str, float]:
    """Плоский набор метрик для бутстрэпа (без счётчиков n)."""
    flat = {k: v for k, v in m.items() if isinstance(v, float)}
    for t, tm in m["per_type"].items():
        flat.update({f"{t}.{k}": v for k, v in tm.items() if isinstance(v, float)})
    return flat


def _bootstrap(rows: list[dict], seed: int = 7) -> dict:
    by_study: dict[str, list[dict]] = {}
    for r in rows:
        by_study.setdefault(r["study"], []).append(r)
    studies = sorted(by_study)
    rng = random.Random(seed)
    acc: dict[str, list[float]] = {}
    for _ in range(BOOTSTRAP):
        sample = [r for _ in studies for r in by_study[rng.choice(studies)]]
        for k, v in _scalars(_metrics(sample)).items():
            acc.setdefault(k, []).append(v)
    return {k: [float(np.percentile(v, 2.5)), float(np.percentile(v, 97.5))] for k, v in acc.items()}


def main() -> None:
    xlsx, studies_dir, raw_path = sys.argv[1], sys.argv[2], sys.argv[3]
    warnings.filterwarnings("ignore")

    examples = build_examples(xlsx, studies_dir)
    cnn_oof = _load_cnn_oof(raw_path, examples)
    studies = sorted({e.study for e in examples})
    splits = group_kfold(studies, N_FOLDS, SEED)  # те же фолды, что у CNN

    spine_feats = {}
    for e in examples:
        if e.region == "spine" and all(f in e.labels for f in SPINE_FIELDS):
            img8 = auto_windowed_uint8(get_pixel_array(read_dicom(e.path)))
            mask, bbox = _segment_main_object(img8)
            if bbox is not None:
                spine_feats[e.path] = learned_scorers.spine_features(img8, mask)

    # CNN не запускаем: подставляем её OOF-вероятность для данного снимка
    hybrid._positioning_probability = lambda path, region: cnn_oof.get(path)

    def run(example, engine) -> dict:
        row = process_file(Path(example.path), engine)
        return {"study": example.study, "path": example.path, "region": example.region,
                "truth": _truth(example), "status": row["processing_status"],
                "prob": float(row["quality_prob"]) if row["quality_prob"] != "" else None,
                "cls": int(row["quality_class"]) if row["quality_class"] != "" else 0,
                "types": set(t for t in row["violation_type"].split(";") if t)}

    deployed_thr = hybrid.POSITIONING_THRESHOLD
    rule_thr = _cnn_threshold(cnn_oof, examples)
    print(f"порог CNN по правилу на всех OOF: {rule_thr:.4f} (в hybrid.py: {deployed_thr})", flush=True)
    if abs(rule_thr - deployed_thr) > 1e-3:
        print("  ВНИМАНИЕ: hybrid.POSITIONING_THRESHOLD не совпадает с правилом — обновите константу", flush=True)

    results: dict[str, list[dict]] = {"old": [], "heuristics": [], "hybrid_thr05": [], "hybrid": []}
    fold_thresholds = []

    heuristics.USE_LEARNED_SPINE_SCORERS = False
    results["old"] = [run(e, heuristics) for e in examples]
    heuristics.USE_LEARNED_SPINE_SCORERS = True

    for fold_i, (train_studies, val_studies) in enumerate(splits):
        tr = [e for e in examples if e.study in train_studies and e.path in spine_feats]
        learned_scorers._cache = _spine_spec([spine_feats[e.path] for e in tr],
                                             [{f: int(e.labels[f]) for f in SPINE_FIELDS} for e in tr])
        val = [e for e in examples if e.study in val_studies]
        results["heuristics"] += [run(e, heuristics) for e in val]
        hybrid.POSITIONING_THRESHOLD = 0.5
        results["hybrid_thr05"] += [run(e, hybrid) for e in val]
        hybrid.POSITIONING_THRESHOLD = _cnn_threshold(cnn_oof, examples, exclude_studies=val_studies)
        fold_thresholds.append(hybrid.POSITIONING_THRESHOLD)
        results["hybrid"] += [run(e, hybrid) for e in val]
        print(f"fold {fold_i}: train spine={len(tr)} val={len(val)} "
              f"порог CNN={hybrid.POSITIONING_THRESHOLD:.4f}", flush=True)
    learned_scorers._cache = None
    hybrid.POSITIONING_THRESHOLD = deployed_thr

    report = {"n_images": len(examples), "n_studies": len(studies), "folds": N_FOLDS, "seed": SEED,
              "bootstrap": BOOTSTRAP, "cnn_threshold_rule_all_oof": rule_thr,
              "cnn_threshold_per_fold": fold_thresholds, "configs": {}}
    for name, rows in results.items():
        cfg = {}
        for scope in ("all", "spine", "hip"):
            sub = rows if scope == "all" else [r for r in rows if r["region"] == scope]
            m = _metrics(sub)
            m["ci95"] = _bootstrap(sub)
            cfg[scope] = m
        report["configs"][name] = cfg
        a = cfg["all"]
        print(f"{name}: AUC {a['quality_prob_roc_auc']:.3f} {np.round(a['ci95']['quality_prob_roc_auc'], 3)} | "
              f"F1 {a['quality_class_f1']:.3f} {np.round(a['ci95']['quality_class_f1'], 3)} | "
              f"sens {a['quality_class_sensitivity']:.3f} spec {a['quality_class_specificity']:.3f} | "
              f"macro-F1 {a['violation_type_macro_f1']:.3f}", flush=True)

    out = Path(__file__).resolve().parent.parent.parent / "docs" / "pipeline_cv.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"-> {out}")


if __name__ == "__main__":
    main()
