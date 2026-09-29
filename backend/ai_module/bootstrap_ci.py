"""
95% доверительные интервалы для ROC-AUC (шаг 4/7 плана переделки,
`Документация/4. ДепЗдрав.pdf`, п.8.4: "Метрики диагностической точности
желательно представлять с 95% доверительными интервалами").

Бутстрэп ПО ИССЛЕДОВАНИЯМ, не по снимкам — тот же принцип, что и в
кросс-валидации `model_train.py` (иначе несколько снимков одного пациента
в одном ресэмпле создают иллюзию бОльшей выборки, чем есть на самом деле).

Вход — `../_extract/model_vs_heuristic_raw.json` (сырые OOF-предсказания,
сохранённые `model_train.py`). Отдельный скрипт, а не часть model_train.py,
чтобы не требовать повторного обучения при пересчёте ДИ.
"""
from __future__ import annotations

import json
import random

import numpy as np

N_BOOTSTRAP = 2000
SEED = 42


def roc_auc(y_true: np.ndarray, y_score: np.ndarray) -> float | None:
    y_true = np.asarray(y_true, dtype=bool)
    y_score = np.asarray(y_score, dtype=float)
    pos = y_score[y_true]
    neg = y_score[~y_true]
    if len(pos) == 0 or len(neg) == 0:
        return None
    greater = (pos[:, None] > neg[None, :]).sum()
    ties = (pos[:, None] == neg[None, :]).sum()
    return float((greater + 0.5 * ties) / (len(pos) * len(neg)))


def bootstrap_auc_ci(study_ids: list[str], y_true: list[int], scores: list[float],
                      n_bootstrap: int = N_BOOTSTRAP, seed: int = SEED) -> dict:
    rng = random.Random(seed)
    study_ids = np.array(study_ids)
    y_true = np.array(y_true)
    scores = np.array(scores)
    unique_studies = sorted(set(study_ids))

    idx_by_study = {s: np.where(study_ids == s)[0] for s in unique_studies}

    point_estimate = roc_auc(y_true, scores)
    boot_aucs = []
    for _ in range(n_bootstrap):
        sampled_studies = [rng.choice(unique_studies) for _ in unique_studies]
        idx = np.concatenate([idx_by_study[s] for s in sampled_studies])
        auc = roc_auc(y_true[idx], scores[idx])
        if auc is not None:
            boot_aucs.append(auc)

    if not boot_aucs:
        return {"point": point_estimate, "ci_low": None, "ci_high": None,
                "n_studies": len(unique_studies), "valid_resamples": 0}

    ci_low, ci_high = np.percentile(boot_aucs, [2.5, 97.5])
    return {
        "point": point_estimate,
        "ci_low": float(ci_low), "ci_high": float(ci_high),
        "n_studies": len(unique_studies), "valid_resamples": len(boot_aucs),
    }


def main() -> None:
    with open("../_extract/model_vs_heuristic_raw.json", encoding="utf-8") as f:
        raw = json.load(f)

    report = {}
    for field, data in raw.items():
        model_ci = bootstrap_auc_ci(data["study"], data["y_true"], data["model_score"])
        heur_ci = bootstrap_auc_ci(data["study"], data["y_true"], data["heuristic_score"])
        report[field] = {"model": model_ci, "heuristic": heur_ci}
        print(f"{field}: n_studies={model_ci['n_studies']}")
        print(f"  model:     AUC={model_ci['point']:.3f}  95% CI [{model_ci['ci_low']:.3f}, {model_ci['ci_high']:.3f}]")
        print(f"  heuristic: AUC={heur_ci['point']:.3f}  95% CI [{heur_ci['ci_low']:.3f}, {heur_ci['ci_high']:.3f}]")

    with open("../docs/model_vs_heuristic_ci.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
