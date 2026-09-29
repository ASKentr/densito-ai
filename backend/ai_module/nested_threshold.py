"""
Вложенная кросс-валидация порога CNN укладки бедра (ответ на внешнее ревью
2026-09-29, замечание 3).

Проблема прежней оценки (`eval_pipeline_cv.py`): порог для внешнего фолда A
считался по OOF-вероятностям фолдов B–E, но модели, выдавшие эти
вероятности, обучались в том числе на A — косвенная утечка A в порог.

Здесь для каждого внешнего фолда k:
  1. берём только его обучающую часть T_k (исследования вне фолда k);
  2. делим T_k на 4 внутренних фолда по исследованиям, на каждом обучаем CNN
     (та же процедура `model_train.train_one_fold`) и получаем внутренние
     OOF-вероятности укладки бедра — ни одна из этих моделей не видела фолд k;
  3. порог_k = то же правило, что в боевом решении: квантиль внутренних
     OOF-вероятностей уровня 1 − доля нарушений укладки в T_k.
Внешний фолд затем оценивается один раз — моделью фолда k (её вероятности —
прежние OOF из `model_train.py`, она обучалась ровно на T_k) с порогом_k:
`eval_pipeline_cv.py ... --nested docs/cnn_nested_thresholds.json`.

    python -m ai_module.nested_threshold "$XLSX" "$STUDIES"   # ~1 ч на CPU (20 обучений)
    -> ../docs/cnn_nested_thresholds.json (пишется после каждого внешнего фолда)
"""
from __future__ import annotations

import json
import random
import sys
import time
import warnings
from pathlib import Path

import numpy as np
import torch

from ai_module.model_data import HIP_FIELDS, build_examples
from ai_module.model_train import N_FOLDS, SEED, group_kfold, train_one_fold

INNER_FOLDS = 4
FIELD = "incorrect_positioning"
OUT = Path(__file__).resolve().parent.parent.parent / "docs" / "cnn_nested_thresholds.json"


def _seed(s: int) -> None:
    random.seed(s)
    np.random.seed(s)
    torch.manual_seed(s)


def main() -> None:
    xlsx, studies_dir = sys.argv[1], sys.argv[2]
    warnings.filterwarnings("ignore")
    torch.set_num_threads(max(1, torch.get_num_threads()))

    examples = build_examples(xlsx, studies_dir)
    studies = sorted({e.study for e in examples})
    outer = group_kfold(studies, N_FOLDS, SEED)            # те же фолды, что в model_train / eval
    fi = HIP_FIELDS.index(FIELD)
    report = {"method": "nested CV: порог по внутренним OOF обучающей части внешнего фолда",
              "rule": "квантиль уровня 1 - доля нарушений укладки бедра",
              "outer_folds": N_FOLDS, "inner_folds": INNER_FOLDS, "seed": SEED, "folds": []}

    for k, (train_studies, val_studies) in enumerate(outer):
        t0 = time.time()
        inner_studies = sorted(train_studies)
        inner = group_kfold(inner_studies, INNER_FOLDS, SEED + 100 + k)
        probs, labels = [], []
        for j, (itr, iva) in enumerate(inner):
            _seed(SEED + 10 * k + j)
            tr = [e for e in examples if e.study in itr]
            va = [e for e in examples if e.study in iva]
            _, hip_probs = train_one_fold(tr, va)
            for e, p in zip(va, hip_probs):
                if e.region == "hip" and FIELD in e.labels:
                    probs.append(float(p[fi]))
                    labels.append(int(e.labels[FIELD]))
            print(f"outer {k} inner {j}: train={len(tr)} val={len(va)} ({time.time() - t0:.0f} c)", flush=True)
        p = np.array(probs)
        prevalence = float(np.mean(labels))
        thr = float(np.quantile(p, 1 - prevalence))
        report["folds"].append({"fold": k, "threshold": thr, "n_hip_labeled": len(p),
                                "prevalence": prevalence, "train_studies": len(train_studies),
                                "seconds": round(time.time() - t0)})
        OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"outer {k}: порог = {thr:.4f} (доля нарушений {prevalence:.3f}, n={len(p)})", flush=True)

    print(f"saved {OUT}")


if __name__ == "__main__":
    main()
