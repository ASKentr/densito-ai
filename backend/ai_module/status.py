"""
Самопроверка ИИ-модуля: готов ли боевой движок (гибрид) к анализу.

Проверяется каждая часть, от которой зависит результат:
  - правила ТЗ (эвристики) — импорт модуля;
  - модели позвоночника — наличие и чтение `weights/spine_scorers.json`;
  - нейросеть укладки бедра — импорт PyTorch, загрузка весов
    `weights/violation_model.pt` и пробный прогон на пустом кадре.

Веса загружаются один раз и остаются в памяти (тот же объект использует
анализ), поэтому повторные проверки дешёвые. Результат отдаёт `GET /health`,
его показывает индикатор в шапке веб-интерфейса.
"""
from __future__ import annotations

import time


def _component(name: str, check) -> dict:
    start = time.perf_counter()
    try:
        detail = check() or ""
        ok = True
    except Exception as exc:  # любая причина неготовности — в ответ, а не исключением
        ok, detail = False, f"{type(exc).__name__}: {exc}"
    return {"name": name, "ok": ok, "detail": detail, "ms": round((time.perf_counter() - start) * 1000, 1)}


def _check_rules():
    from ai_module import heuristics  # noqa: F401
    return "ось ≤ 5°, область интереса, определение области"


def _check_spine():
    from ai_module import learned_scorers
    if not learned_scorers.available():
        raise FileNotFoundError(f"нет файла {learned_scorers.WEIGHTS_PATH.name}")
    scorers = learned_scorers._load().get("scorers", {})
    return f"логистических моделей: {len(scorers)} · {learned_scorers.WEIGHTS_PATH.name}"


def _check_cnn():
    import torch
    from ai_module import hybrid
    from ai_module.model import IMAGE_SIZE

    if not hybrid.WEIGHTS_PATH.exists():
        raise FileNotFoundError(f"нет файла весов {hybrid.WEIGHTS_PATH.name}")
    model = hybrid._get_model()
    with torch.no_grad():
        model(torch.zeros(1, 3, IMAGE_SIZE, IMAGE_SIZE))
    return f"ResNet18 · PyTorch {torch.__version__} · CPU"


def engine_status() -> dict:
    components = [
        _component("Правила ТЗ", _check_rules),
        _component("Модели позвоночника", _check_spine),
        _component("Нейросеть укладки бедра", _check_cnn),
    ]
    try:
        from ai_module.hybrid import MODEL_VERSION
    except Exception:
        MODEL_VERSION = ""
    return {
        "ready": all(c["ok"] for c in components),
        "model_version": MODEL_VERSION,
        "components": components,
    }
